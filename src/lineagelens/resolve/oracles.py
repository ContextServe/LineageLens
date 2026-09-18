"""Tier B resolver oracles (issue #51 §7.2).

Tier B is **required**, not an enhancement. Tier A cannot resolve a Java
overload, a Go structurally-satisfied interface, or a TypeScript structural
type -- and schema 3's response to an unanswerable reference was to guess,
which is how it produced 2,593 Dubbo edges that all claimed
``resolution=resolved`` and none of which traversed.

The interface is deliberately two methods::

    resolve(ref)   -> ResolvedTarget | None    what does this name point at
    type_of(span)  -> TypeName | None           what type is this expression

That narrowness is what makes six language toolchains tractable. An oracle
never mints node ids, never writes edges, and never describes structure -- it
answers those two questions and the resolver does the rest.

Toolchain policy
----------------

Resolution order per language, from ``§7.2``:

1. **Vendored** -- ships as a Python wheel, so no external requirement. jedi
   (Python) is here; a bundled ``tsserver`` for TypeScript belongs here too.
2. **Detected** -- a system toolchain found on PATH or via an env var
   (``javac``, ``gopls``, ``rust-analyzer``, ``dotnet``).
3. **Container** -- ``--toolchain-image`` runs a missing resolver in Docker.
4. **Refuse that language** -- files are recorded ``skip_reason='missing_tier_b'``
   and the capability matrix marks the language unavailable. They are *not*
   extracted at Tier A only, because a Tier-A-only Java graph is exactly the
   fabricated-edge failure above.

``--allow-tier-a-only=<langs>`` overrides step 4 explicitly. Every edge it
produces is stamped ``heuristic`` / ``tier_a_only`` and reported in the
completeness envelope, so the degradation is never silent.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dcfield
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core import Evidence, NodeKind, Resolution, ResolvedTarget, Span, UnresolvedRef

logger = logging.getLogger(__name__)

#: Sentinel for "PATH not yet probed". Distinct from None, which means probed
#: and not found.
_UNPROBED = object()


class OracleAvailability(str):
    """How an oracle became available, for the capability matrix (§12)."""

    VENDORED = "vendored"
    DETECTED = "detected"
    CONTAINER = "container"
    MISSING = "missing"


@runtime_checkable
class ResolverOracle(Protocol):
    """A language toolchain, asked only what it is uniquely able to answer."""

    name: str
    languages: frozenset[str]

    def available(self) -> str:
        """One of the :class:`OracleAvailability` values."""
        ...

    def version(self) -> str:
        ...

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        ...

    def type_of(self, span: Span, file_path: str) -> str | None:
        ...


@dataclass(slots=True)
class _Base:
    """Shared plumbing. Subclasses implement the two answering methods."""

    project_root: Path = Path()

    def version(self) -> str:
        return "unknown"

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        return None

    def type_of(self, span: Span, file_path: str) -> str | None:
        return None


@dataclass(slots=True)
class JediOracle(_Base):
    """Python, via jedi. Vendored: jedi is a wheel, so nothing external needed.

    jedi is genuinely useful for the case Tier A cannot reach -- inferring the
    type of a receiver that was never annotated, so ``repo.save()`` resolves
    when ``repo`` came from a factory rather than from a typed parameter.
    """

    name: str = "jedi"
    languages: frozenset[str] = frozenset({"python"})

    def available(self) -> str:
        try:
            import jedi  # noqa: F401
        except ImportError:
            return OracleAvailability.MISSING
        return OracleAvailability.VENDORED

    def version(self) -> str:
        try:
            import jedi

            return getattr(jedi, "__version__", "unknown")
        except ImportError:
            return "missing"

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        """Ask jedi what a name at a position refers to.

        Returns ``None`` rather than a guess whenever jedi is unsure or offers
        more than one answer: an ambiguous oracle answer is no better than an
        ambiguous Tier A candidate set, and the resolver already knows how to
        record that honestly.
        """
        try:
            import jedi
        except ImportError:
            return None

        path = self.project_root / ref.file_path
        if not path.is_file():
            return None

        try:
            script = jedi.Script(path=str(path), project=jedi.Project(str(self.project_root)))
            names = script.goto(
                line=ref.span.start_line,
                column=ref.span.start_col,
                follow_imports=True,
            )
        except Exception as exc:  # jedi raises a wide variety on odd input
            logger.debug("jedi could not resolve %s at %s: %s", ref.ref_text, path, exc)
            return None

        in_project = [n for n in names if n.module_path and self._inside(n.module_path)]
        if len(in_project) != 1:
            # Zero means external; more than one means jedi is guessing too.
            return None

        found = in_project[0]
        return ResolvedTarget(
            qualified_name=found.full_name or found.name,
            kind=_JEDI_KINDS.get(found.type),
            evidence=Evidence.fact("jedi_goto"),
            resolution=Resolution.EXACT,
        )

    def type_of(self, span: Span, file_path: str) -> str | None:
        try:
            import jedi
        except ImportError:
            return None
        path = self.project_root / file_path
        if not path.is_file():
            return None
        try:
            script = jedi.Script(path=str(path), project=jedi.Project(str(self.project_root)))
            inferred = script.infer(line=span.start_line, column=span.start_col)
        except Exception:
            return None
        return inferred[0].name if len(inferred) == 1 else None

    def _inside(self, module_path: Path) -> bool:
        try:
            Path(module_path).relative_to(self.project_root)
        except ValueError:
            return False
        return True


_JEDI_KINDS: dict[str, NodeKind] = {
    "class": NodeKind.CLASS,
    "function": NodeKind.FUNCTION,
    "module": NodeKind.MODULE,
    "instance": NodeKind.VARIABLE,
    "param": NodeKind.PARAMETER,
    "statement": NodeKind.VARIABLE,
    "property": NodeKind.PROPERTY,
}


@dataclass(slots=True)
class ToolchainOracle(_Base):
    """A detected system toolchain.

    Present as a real availability check with no answering implementation yet:
    ``resolve`` inherits the base's ``None``. That is deliberate rather than a
    stub -- it lets the capability matrix report the honest state ("javac
    detected, resolution not wired") instead of either claiming the language is
    unsupported or silently answering from Tier A and calling it a fact.

    Wiring each toolchain is per-language work and is tracked separately; §15
    lists why each is irreducibly language-specific.
    """

    name: str = "toolchain"
    languages: frozenset[str] = frozenset()
    executable: str = ""
    env_var: str = ""
    #: Memoised result of the PATH lookup. A toolchain does not appear or vanish
    #: mid-run, and the registry consults ``available()`` once per *reference* --
    #: profiling an index of 288 files showed 29,818 ``shutil.which`` calls, each
    #: a directory scan, for 10% of total runtime. Sentinel is a bare object()
    #: because ``None`` is a meaningful answer here (not found).
    _located: Path | object | None = _UNPROBED

    def available(self) -> str:
        if self._locate() is None:
            return OracleAvailability.MISSING
        return OracleAvailability.DETECTED

    def version(self) -> str:
        located = self._locate()
        return str(located) if located else "missing"

    def _locate(self) -> Path | None:
        if self._located is not _UNPROBED:
            return self._located  # type: ignore[return-value]

        found: Path | None = None
        if self.env_var:
            root = os.environ.get(self.env_var)
            if root:
                candidate = Path(root) / "bin" / self.executable
                if candidate.exists():
                    found = candidate
        if found is None and self.executable:
            which = shutil.which(self.executable)
            found = Path(which) if which else None

        self._located = found
        return found


@dataclass(slots=True)
class ScipOracle(_Base):
    """Compiler-verified resolution from a SCIP index (#47).

    An *oracle*, not a merger. The original design built a parallel graph from
    SCIP and reconciled two graphs by ``(file, line, col)``; implementing this
    protocol instead inherits the evidence tiers, the availability matrix,
    ``--require-tier-b`` semantics and ``unresolved_refs`` as a ready-made work
    list. SCIP answers "what does this name point at", which is exactly and
    only what an oracle is asked.

    **Staleness is a hard gate, per file.** An index built against a different
    commit than the working tree is refused rather than trusted: each SCIP
    document's text is hashed against ``files.content_hash`` and a file whose
    hash differs gets no SCIP answers and falls back to Tier A. Without this,
    SCIP would reintroduce precisely the failure #51 was written to eliminate --
    confident edges that do not correspond to the code on disk.

    **Nothing is subprocessed.** LineageLens does not run ``scip-java`` or
    ``scip-typescript``. Running a build tool inside ``index`` would make
    indexing network-dependent, slow and non-deterministic, and
    ``verify --determinism`` could not hold. When a toolchain is detected and no
    index is present, the CLI prints the command to generate one.
    """

    name: str = "scip"
    languages: frozenset[str] = frozenset()
    index_path: Path | None = None
    #: ``{(path, line, col): symbol}`` for every occurrence.
    _occurrences: dict[tuple[str, int, int], str] = dcfield(default_factory=dict)
    #: ``{symbol: (path, line, col)}`` for definition occurrences only.
    _definitions: dict[str, tuple[str, int, int]] = dcfield(default_factory=dict)
    #: Files whose SCIP text does not match what is on disk. Answers refused.
    _stale: frozenset[str] = frozenset()
    _tool: str = ""
    _loaded: bool = False
    _error: str = ""
    #: ``{path: content_hash}`` from the store, supplied by the indexer so the
    #: oracle can gate on staleness without reaching into the database itself.
    content_hashes: dict[str, str] = dcfield(default_factory=dict)

    def available(self) -> str:
        self._load()
        if self._error or not self._occurrences:
            return OracleAvailability.MISSING
        return OracleAvailability.DETECTED

    def can_resolve(self) -> bool:
        return self.available() != OracleAvailability.MISSING

    def version(self) -> str:
        self._load()
        return self._tool or "unknown"

    def status(self) -> dict[str, object]:
        """What this index covers, for the coverage envelope and `ontology`."""
        self._load()
        return {
            "tool": self._tool,
            "occurrences": len(self._occurrences),
            "definitions": len(self._definitions),
            "documents": self._documents,
            "stale_files": sorted(self._stale),
            "error": self._error,
        }

    _documents: int = 0

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.index_path is None:
            self._error = "no index path"
            return

        from .scip import ScipParseError, read_index

        try:
            index = read_index(self.index_path)
        except ScipParseError as exc:
            # Recorded, not raised. A bad SCIP index must degrade to Tier A,
            # not fail the whole build -- but it must say so rather than
            # silently contributing nothing.
            self._error = str(exc)
            logger.warning("SCIP index unusable, falling back to Tier A: %s", exc)
            return

        self._tool = index.tool
        self._documents = len(index.documents)
        stale: set[str] = set()

        for document in index.documents:
            path = self._normalise(document.relative_path)
            if self._is_stale(path, document.text):
                stale.add(path)
                continue
            for occurrence in document.occurrences:
                key = (path, occurrence.line, occurrence.column)
                self._occurrences[key] = occurrence.symbol
                if occurrence.is_definition:
                    self._definitions.setdefault(occurrence.symbol, key)

        self._stale = frozenset(stale)
        if stale:
            logger.warning(
                "SCIP index is stale for %d file(s); those fall back to Tier A",
                len(stale),
            )

    def _normalise(self, relative_path: str) -> str:
        """SCIP paths are project-root-relative with forward slashes."""
        return relative_path.replace("\\", "/").lstrip("./")

    def _is_stale(self, path: str, text: str) -> bool:
        """Does this document describe the file currently on disk?

        Only decidable when SCIP carried the text *and* the store recorded a
        hash. When either is absent the file is trusted -- an unverifiable
        index is not evidence of staleness, and refusing everything
        unverifiable would make SCIP useless against indexers that omit text.
        """
        if not text:
            return False
        expected = self.content_hashes.get(path)
        if not expected:
            return False
        import hashlib

        actual = hashlib.blake2b(text.encode(), digest_size=16).hexdigest()
        return actual != expected

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        self._load()
        path = self._normalise(ref.file_path)
        if path in self._stale:
            return None

        symbol = self._occurrences.get(
            (path, ref.span.start_line, ref.span.start_col)
        )
        if symbol is None:
            return None

        definition = self._definitions.get(symbol)
        if definition is None:
            # SCIP saw the reference but not a definition: it points into a
            # dependency this index did not define. Reporting it as external
            # is strictly better than reporting nothing -- "definitely
            # external" and "unknown" are different answers.
            return ResolvedTarget(
                qualified_name=symbol,
                is_external=True,
                evidence=Evidence.fact("scip_external"),
                resolution=Resolution.EXTERNAL,
            )

        def_path, def_line, _ = definition
        return ResolvedTarget(
            qualified_name=f"{def_path}:{def_line}",
            evidence=Evidence.fact("scip_resolve"),
            resolution=Resolution.EXACT,
        )

    def type_of(self, span: Span, file_path: str) -> str | None:
        """The SCIP symbol at a span.

        Covers the TypeScript structural-type and Java overload cases Tier A
        cannot reach, because the symbol string encodes the resolved type
        rather than the written one.
        """
        self._load()
        path = self._normalise(file_path)
        if path in self._stale:
            return None
        return self._occurrences.get((path, span.start_line, span.start_col))


def default_oracles(
    project_root: Path,
    scip_index: Path | None = None,
    scip_languages: frozenset[str] | None = None,
    content_hashes: dict[str, str] | None = None,
) -> list[ResolverOracle]:
    """Every oracle this build knows about, in the order §7.2 prescribes.

    SCIP goes first when an index is present: a compiler-verified fact
    outranks a static-analysis inference, so it answers before jedi even for
    Python. ``OracleRegistry.resolve`` takes the first answer, so order *is*
    the precedence rule -- this is the one place #47 changes existing
    behaviour.
    """
    oracles: list[ResolverOracle] = []
    if scip_index is not None:
        oracles.append(ScipOracle(
            project_root=project_root,
            index_path=scip_index,
            languages=scip_languages or frozenset(
                {"java", "typescript", "javascript", "python", "go",
                 "rust", "csharp"}
            ),
            content_hashes=dict(content_hashes or {}),
        ))
    oracles.extend([
        JediOracle(project_root=project_root),
        ToolchainOracle(project_root=project_root, name="javac",
                        languages=frozenset({"java"}),
                        executable="javac", env_var="JAVA_HOME"),
        ToolchainOracle(project_root=project_root, name="tsserver",
                        languages=frozenset({"typescript", "javascript"}),
                        executable="tsc"),
        ToolchainOracle(project_root=project_root, name="gopls",
                        languages=frozenset({"go"}), executable="gopls"),
        ToolchainOracle(project_root=project_root, name="rust-analyzer",
                        languages=frozenset({"rust"}), executable="rust-analyzer"),
        ToolchainOracle(project_root=project_root, name="roslyn",
                        languages=frozenset({"csharp"}), executable="dotnet"),
    ])
    return oracles


class OracleRegistry:
    """Dispatches a reference to whichever oracle covers its language."""

    def __init__(
        self,
        oracles: Iterable[ResolverOracle] | None = None,
        *,
        project_root: Path | None = None,
        lang_of_file: dict[str, str] | None = None,
    ) -> None:
        root = project_root or Path()
        self._oracles = list(oracles) if oracles is not None else default_oracles(root)
        #: ``{file_path: lang}``, so a reference can be routed without the
        #: resolver having to carry language on every record.
        self._lang_of_file = lang_of_file or {}
        self._by_lang: dict[str, list[ResolverOracle]] = {}
        for oracle in self._oracles:
            for lang in oracle.languages:
                self._by_lang.setdefault(lang, []).append(oracle)

    def resolve(self, ref: UnresolvedRef) -> ResolvedTarget | None:
        lang = self._lang_of_file.get(ref.file_path)
        if lang is None:
            return None
        for oracle in self._by_lang.get(lang, ()):
            if oracle.available() == OracleAvailability.MISSING:
                continue
            answer = oracle.resolve(ref)
            if answer is not None:
                return answer
        return None

    def availability(self) -> dict[str, dict[str, str]]:
        """``{lang: {oracle: availability}}`` for the capability matrix (§12).

        Reported per install, not per release: whether Java has type-accurate
        resolution depends on whether a JDK is present on *this* machine, and an
        agent reading the matrix needs to know that before trusting a negative
        answer about Java.
        """
        matrix: dict[str, dict[str, str]] = {}
        for lang, oracles in sorted(self._by_lang.items()):
            matrix[lang] = {
                oracle.name: oracle.available()
                for oracle in sorted(oracles, key=lambda o: o.name)
            }
        return matrix

    def languages_without_tier_b(self) -> list[str]:
        """Languages where no oracle is available -- §7.2 step 4 applies."""
        missing: list[str] = []
        for lang, oracles in sorted(self._by_lang.items()):
            if all(o.available() == OracleAvailability.MISSING for o in oracles):
                missing.append(lang)
        return missing
