"""Builds an index: walk, extract, resolve, store.

This is the orchestrator that replaces ``cli.build``. The behavioural change
that matters is at the top of :meth:`Indexer.run`: files are grouped by
language and *all* of them are extracted into one graph.

Schema 3 dispatched on the whole repository with an early return -- JS won, then
Java, then Python -- so a polyglot repo produced one language and silently
discarded the rest. Running it on this repository emitted 39 TypeScript symbols
and zero Python, overwriting a 759-symbol graph in place with no warning.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from .core import (
    DataflowMode,
    FileRecord,
    Observation,
    ParseStatus,
    SkipReason,
    normalise_module_path,
)
from .extract import (
    ParserRegistry,
    SpecExtractor,
    SpecRegistry,
    detect_dialect,
    language_of,
)
from .resolve import OracleRegistry, Resolver, SymbolIndex
from .services import IGNORED_DIRS, ServiceLocator, discover_services
from .store import DB_FILENAME, GraphStore

logger = logging.getLogger(__name__)

#: Files above this size are recorded as skipped rather than parsed. Generated
#: bundles and vendored blobs are the usual cause, and parsing a 10 MB minified
#: file costs more than it can possibly contribute.
MAX_FILE_BYTES = 2_000_000

#: Path fragments that mark generated or vendored code. Recorded with a reason
#: rather than dropped, so the ledger can say why (§10.6).
GENERATED_MARKERS = (
    ".min.js", ".min.css", ".bundle.js", ".generated.", "_pb2.py", "_pb.go",
    ".pb.go", ".g.cs", ".designer.cs", ".d.ts",
)


@dataclass(slots=True)
class IndexReport:
    """What one index run did, for the CLI and the acceptance tests."""

    project_root: str
    files_seen: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    nodes: int = 0
    edges: int = 0
    unresolved: int = 0
    boundaries: int = 0
    services: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    build_digest: str = ""
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "project_root": self.project_root,
            "files": {
                "seen": self.files_seen, "parsed": self.files_parsed,
                "skipped": self.files_skipped, "failed": self.files_failed,
            },
            "graph": {
                "nodes": self.nodes, "edges": self.edges,
                "unresolved_refs": self.unresolved, "boundaries": self.boundaries,
            },
            "services": self.services,
            "languages": dict(sorted(self.languages.items())),
            "skipped_reasons": dict(sorted(self.skipped_reasons.items())),
            "build_digest": self.build_digest,
            "duration_seconds": round(self.duration_seconds, 3),
        }


class Indexer:
    """Walks a project and writes a schema-4 graph."""

    def __init__(
        self,
        project_root: Path,
        *,
        dataflow: DataflowMode = DataflowMode.LAZY,
        allow_tier_a_only: frozenset[str] = frozenset(),
    ) -> None:
        self.root = project_root.resolve()
        self.dataflow = dataflow
        #: Languages the caller has explicitly accepted at Tier A only (§7.2).
        #: Everything else with no available oracle is skipped rather than
        #: resolved by name matching.
        self.allow_tier_a_only = allow_tier_a_only
        self.specs = SpecRegistry()
        self.parsers = ParserRegistry()
        self.extractor = SpecExtractor(specs=self.specs, parsers=self.parsers)

    # ---- entry point ------------------------------------------------------

    def run(self, *, db_path: Path | None = None) -> tuple[GraphStore, IndexReport]:
        started = datetime.now(UTC)
        report = IndexReport(project_root=str(self.root))

        services = discover_services(self.root)
        locator = ServiceLocator(services)
        report.services = len(services)

        oracles = OracleRegistry(project_root=self.root)
        refused = {
            lang for lang in oracles.languages_without_tier_b()
            if lang not in self.allow_tier_a_only
        }
        if refused:
            # §7.2 step 4. Refusal rather than degradation, because a
            # Tier-A-only Java graph is the fabricated-edge failure of §1.2:
            # 2,593 edges all claiming resolution=resolved, none traversable.
            logger.warning(
                "no Tier B resolver for %s; files in these languages will be "
                "skipped. Override per language with --allow-tier-a-only.",
                ", ".join(sorted(refused)),
            )

        observations: list[Observation] = []
        lang_of_file: dict[str, str] = {}

        for rel_path, content, dialect in self._walk():
            report.files_seen += 1
            lang = language_of(dialect)
            service = locator.for_path(rel_path)
            digest = hashlib.blake2b(content, digest_size=16).hexdigest()

            skip = self._skip_reason(rel_path, content, lang, refused)
            if skip is not None:
                observations.append(Observation(file=FileRecord(
                    path=rel_path, lang=lang, content_hash=digest,
                    size_bytes=len(content), parse_status=ParseStatus.SKIPPED,
                    service_id=service.id if service else None, skip_reason=skip,
                )))
                report.files_skipped += 1
                report.skipped_reasons[skip.value] = (
                    report.skipped_reasons.get(skip.value, 0) + 1
                )
                continue

            observation = self.extractor.extract(
                path=rel_path,
                content=content,
                dialect=dialect,
                content_hash=digest,
                service=service.name if service else "",
                module_path=self._module_path(rel_path, service.root if service else "."),
                service_id=service.id if service else None,
            )
            observations.append(observation)
            lang_of_file[rel_path] = lang
            report.languages[lang] = report.languages.get(lang, 0) + 1

            if observation.file.parse_status is ParseStatus.SKIPPED:
                report.files_skipped += 1
                reason = observation.file.skip_reason
                if reason:
                    report.skipped_reasons[reason.value] = (
                        report.skipped_reasons.get(reason.value, 0) + 1
                    )
            elif observation.file.parse_status is ParseStatus.FAILED:
                report.files_failed += 1
            else:
                report.files_parsed += 1

        # Resolution needs the whole node set: a call can target any file.
        all_nodes = [n for o in observations for n in o.nodes]
        index = SymbolIndex(all_nodes)
        resolver = Resolver(
            index,
            oracles=OracleRegistry(project_root=self.root, lang_of_file=lang_of_file),
        )
        resolved = resolver.resolve(observations)

        store = self._write(
            db_path or self.root / ".lineagelens" / DB_FILENAME,
            services, observations, resolved,
        )

        counts = store.counts()
        report.nodes = counts["nodes"]
        report.edges = counts["edges"]
        report.unresolved = counts["unresolved_refs"]
        report.boundaries = counts["boundaries"]
        report.build_digest = store.finalise(
            grammar_digest=self.parsers.digest(),
            spec_digest=self.specs.digest(),
            built_at=started.isoformat(),
        )
        report.duration_seconds = (datetime.now(UTC) - started).total_seconds()

        dangling = store.dangling_edge_count()
        if dangling:
            # §16.1. Foreign keys make this impossible, so a non-zero count
            # means the schema was created without them -- worth failing loudly
            # rather than shipping a graph that cannot be traversed.
            raise RuntimeError(
                f"{dangling} edges have unresolvable endpoints; the graph is not "
                f"traversable. This is the schema-3 Dubbo failure and must never ship."
            )
        return store, report

    # ---- pieces -----------------------------------------------------------

    def _write(self, db_path, services, observations, resolved) -> GraphStore:
        """Persist everything in a handful of batched statements.

        Writes are batched rather than per file. Writing per file issued three
        ``executemany`` calls per file for nodes alone, plus one per distinct
        file for edges and refs -- 1,435 statements for 288 files, and over half
        the total runtime. Each record already carries its own ``file_id``, so
        one call per table is enough.
        """
        store = GraphStore.create(
            db_path, project_root=str(self.root), dataflow_mode=self.dataflow,
        )
        with store.transaction():
            store.write_services(services)

            # File rows first: everything else needs their ids.
            file_ids: dict[str, int] = {}
            for observation in observations:
                file_ids[observation.file.path] = store.write_file(observation.file)

            # Contracts before edges that point at them, and deduped by id since
            # many files may declare the same route. They belong to no file.
            contracts = {c.id: c for c in resolved.contracts}
            if contracts:
                store.write_nodes([c.to_node() for c in contracts.values()])

            store.write_nodes([
                replace(node, file_id=file_ids[observation.file.path])
                for observation in observations
                for node in observation.nodes
            ])

            # Edges only after every node exists: an edge may cross files.
            store.write_edges([
                replace(edge, file_id=file_ids.get(edge.file_path or ""))
                for edge in resolved.edges
            ])
            store.write_unresolved([
                replace(ref, file_id=file_ids.get(ref.file_path))
                for ref in resolved.unresolved
            ])
            store.write_boundaries(resolved.boundaries)
            store.write_coverages([
                (coverage, file_ids[path])
                for path, coverage in resolved.coverage.items()
                if path in file_ids
            ])
        return store

    def _walk(self) -> Iterator[tuple[str, bytes, str]]:
        """Yield ``(rel_path, content, dialect)`` for every source file.

        Language is decided per file. There is no repository-level language, no
        early return, and no ordering in which one language shadows another.
        """
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune in place, and sort, so the walk order is deterministic (§11).
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in IGNORED_DIRS and not d.startswith(".")
            )
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                if path.is_symlink():
                    continue
                try:
                    head = path.open("rb").read(128)
                except OSError:
                    continue
                dialect = detect_dialect(path, head=head)
                if dialect is None:
                    continue
                try:
                    content = path.read_bytes()
                except OSError as exc:
                    logger.debug("cannot read %s: %s", path, exc)
                    continue
                yield (path.relative_to(self.root).as_posix(), content, dialect)

    def _skip_reason(
        self, rel_path: str, content: bytes, lang: str, refused: set[str]
    ) -> SkipReason | None:
        if lang in refused:
            return SkipReason.MISSING_TIER_B
        if len(content) > MAX_FILE_BYTES:
            return SkipReason.TOO_LARGE
        if b"\x00" in content[:8192]:
            return SkipReason.BINARY
        lowered = rel_path.lower()
        if any(marker in lowered for marker in GENERATED_MARKERS):
            return SkipReason.GENERATED
        if not self.specs.has(lang):
            return SkipReason.MISSING_GRAMMAR
        return None

    def _module_path(self, rel_path: str, service_root: str) -> str:
        """Dotted module path for a file, relative to its service.

        Common source roots are stripped so ``src/app/orders.py`` and
        ``app/orders.py`` produce the same module path -- otherwise the same
        symbol would get two identities depending on layout.
        """
        path = rel_path
        if service_root not in ("", ".") and path.startswith(f"{service_root}/"):
            path = path.removeprefix(f"{service_root}/")

        parts = path.split("/")
        strip = {"src", "main", "java", "kotlin", "lib", "app", "source", "test", "tests"}
        while len(parts) > 1 and parts[0] in strip:
            parts.pop(0)

        stem = parts[-1].rsplit(".", 1)[0]
        # A package initialiser names its directory, not itself.
        if stem in ("__init__", "index", "mod", "lib"):
            parts = parts[:-1] or [stem]
        else:
            parts[-1] = stem
        return normalise_module_path(".".join(parts))
