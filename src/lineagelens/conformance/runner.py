"""The conformance suite that generates the capability matrix (issue #51 §12).

Schema 3's ontology was hand-written prose. It declared four relation kinds
while nine were emitted, omitted two that together accounted for 892 edges, and
listed tools that returned nothing. Prose cannot describe behaviour it does not
observe, so this module observes it: one corpus per language exercising every
node kind, edge kind and data-flow rule the spec claims, run through the real
extractor and resolver, with the result written to ``matrix.json``.

The matrix is therefore a *test artifact*. It cannot drift, because generating
it means running the thing it describes. And ``ontology.py`` reports "untested"
rather than inventing a value when the file is absent -- absence of evidence is
reported as absence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import EdgeKind, NodeKind

CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"
MATRIX_PATH = Path(__file__).resolve().parent / "matrix.json"


@dataclass(frozen=True, slots=True)
class Expectation:
    """What a language's corpus should yield, per capability.

    Written as *presence* requirements rather than exact counts. Exact counts
    would break on any spec improvement and would be maintained by copying
    whatever the code produced -- which tests nothing. Presence of a kind is
    the claim the ontology actually makes.
    """

    #: Node kinds the corpus must produce. A language whose corpus omits a
    #: construct simply does not list it.
    nodes: frozenset[NodeKind]
    #: Edge kinds required for each capability to count as supported.
    calls: frozenset[EdgeKind] = frozenset({EdgeKind.CALLS})
    inherits: frozenset[EdgeKind] = frozenset({EdgeKind.INHERITS})
    implements: frozenset[EdgeKind] = frozenset({EdgeKind.IMPLEMENTS})
    dataflow: frozenset[EdgeKind] = frozenset(
        {EdgeKind.READS, EdgeKind.WRITES, EdgeKind.PARAM_BINDS}
    )
    contracts: frozenset[EdgeKind] = frozenset()
    #: Capabilities known to be partial for a language, with the reason. §15
    #: explains why each is irreducible rather than unimplemented.
    partial: dict[str, str] = field(default_factory=dict)


_TYPE_NODES = frozenset({NodeKind.CLASS, NodeKind.METHOD, NodeKind.FIELD,
                         NodeKind.PARAMETER, NodeKind.VARIABLE, NodeKind.CONSTANT})

EXPECTATIONS: dict[str, Expectation] = {
    "python": Expectation(
        nodes=_TYPE_NODES | {NodeKind.CONSTRUCTOR, NodeKind.FUNCTION},
        implements=frozenset(),  # Python has no `implements`; Protocol is a base
        contracts=frozenset({EdgeKind.CONSUMES}),
    ),
    "java": Expectation(
        nodes=_TYPE_NODES | {
            NodeKind.INTERFACE, NodeKind.ENUM, NodeKind.ENUM_MEMBER,
            NodeKind.CONSTRUCTOR, NodeKind.ANNOTATION,
        },
    ),
    "typescript": Expectation(
        nodes=_TYPE_NODES | {
            NodeKind.INTERFACE, NodeKind.TYPE_ALIAS, NodeKind.ENUM,
            NodeKind.CONSTRUCTOR, NodeKind.FUNCTION,
        },
        contracts=frozenset({EdgeKind.CONSUMES}),
    ),
    "javascript": Expectation(
        nodes=frozenset({NodeKind.CLASS, NodeKind.METHOD, NodeKind.FIELD,
                         NodeKind.PARAMETER, NodeKind.VARIABLE,
                         NodeKind.CONSTANT, NodeKind.CONSTRUCTOR}),
        implements=frozenset(),  # no interfaces in the language
        contracts=frozenset({EdgeKind.CONSUMES}),
    ),
    "go": Expectation(
        nodes=frozenset({NodeKind.STRUCT, NodeKind.INTERFACE, NodeKind.METHOD,
                         NodeKind.FUNCTION, NodeKind.FIELD, NodeKind.PARAMETER,
                         NodeKind.VARIABLE, NodeKind.CONSTANT}),
        partial={
            "implements": "interface satisfaction is structural; requires "
                          "whole-program method-set matching (§15)",
        },
    ),
    "rust": Expectation(
        nodes=frozenset({NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ENUM,
                         NodeKind.ENUM_MEMBER, NodeKind.FUNCTION,
                         NodeKind.FIELD, NodeKind.PARAMETER,
                         NodeKind.VARIABLE, NodeKind.CONSTANT}),
        inherits=frozenset(),  # supertraits only, not present in this corpus
        partial={
            "implements": "blanket impls have no single resolvable target (§15)",
        },
    ),
    "csharp": Expectation(
        nodes=frozenset({NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM,
                         NodeKind.ENUM_MEMBER, NodeKind.METHOD,
                         NodeKind.CONSTRUCTOR, NodeKind.PROPERTY,
                         NodeKind.FIELD, NodeKind.PARAMETER,
                         NodeKind.VARIABLE, NodeKind.CONSTANT}),
    ),
}


@dataclass(slots=True)
class LanguageResult:
    """Measured capabilities for one language."""

    lang: str
    node_kinds: dict[str, int] = field(default_factory=dict)
    edge_kinds: dict[str, int] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    missing_nodes: list[str] = field(default_factory=list)
    files: int = 0
    unresolved: int = 0
    #: Capability level derived from which spec files loaded (#56).
    level: str = "L0"
    #: Why the claimed level is not corroborated by what the corpus produced.
    #: Empty when it is. A non-empty value fails the run.
    level_unsupported: str = ""

    def as_dict(self) -> dict[str, Any]:
        out = {
            "files": self.files,
            "level": self.level,
            "node_kinds": dict(sorted(self.node_kinds.items())),
            "edge_kinds": dict(sorted(self.edge_kinds.items())),
            "unresolved_refs": self.unresolved,
            "missing_nodes": self.missing_nodes,
            **self.capabilities,
        }
        if self.level_unsupported:
            out["level_unsupported"] = self.level_unsupported
        return out


def run_language(lang: str, *, corpus_root: Path | None = None) -> LanguageResult:
    """Index one language's corpus and measure what came out."""
    from ..indexer import Indexer

    root = (corpus_root or CORPUS_ROOT) / lang
    result = LanguageResult(lang=lang)
    if not root.is_dir():
        result.capabilities = dict.fromkeys(
            ("nodes", "calls", "inherits", "implements", "dataflow", "contracts"),
            "untested",
        )
        return result

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        # No Tier B requirement: the suite measures what the *specs* express.
        # Whether a JDK happens to be on the machine running it is a separate
        # fact, reported by ontology.installed_tiers().
        store, report = Indexer(root, require_tier_b=frozenset()).run(db_path=Path(tmp) / "conformance.sqlite")
        try:
            result.files = report.files_parsed
            result.node_kinds = store.node_kind_counts()
            result.edge_kinds = store.edge_kind_counts()
            result.unresolved = report.unresolved
        finally:
            store.close()

    # The claimed level, and whether the corpus run corroborates it. A level
    # is derived from which spec files loaded, so a `refs.scm` that does not
    # match its grammar would claim L1 while extracting no call edges --
    # a declared capability wearing a measured one's clothes. Checking it here
    # is what makes the ladder falsifiable rather than decorative (#56 step 2).
    from ..extract.spec import SpecRegistry

    specs = SpecRegistry()
    result.level = specs.spec_for(lang).level if specs.has(lang) else "L0"
    result.level_unsupported = _level_shortfall(result.level, present_edges_of(result))

    expected = EXPECTATIONS.get(lang)
    if expected is None:
        result.capabilities = dict.fromkeys(
            ("nodes", "calls", "inherits", "implements", "dataflow", "contracts"),
            "untested",
        )
        return result

    present_nodes = {k for k, n in result.node_kinds.items() if n}
    present_edges = {k for k, n in result.edge_kinds.items() if n}

    missing = sorted(k.value for k in expected.nodes if k.value not in present_nodes)
    result.missing_nodes = missing
    result.capabilities["nodes"] = not missing

    for capability in ("calls", "inherits", "implements", "dataflow", "contracts"):
        required = getattr(expected, capability)
        if capability in expected.partial:
            result.capabilities[capability] = "partial"
            result.capabilities[f"{capability}_note"] = expected.partial[capability]
        elif not required:
            # Nothing to claim: the language has no such construct, or this
            # corpus does not exercise it. Distinguished from a failure.
            result.capabilities[capability] = "n/a"
        else:
            result.capabilities[capability] = required.issubset(
                {EdgeKind(e) for e in present_edges if e in EdgeKind.__members__
                 or e in {k.value for k in EdgeKind}}
            )
    return result


def present_edges_of(result: LanguageResult) -> set[str]:
    return {kind for kind, count in result.edge_kinds.items() if count}


def _level_shortfall(level: str, present: set[str]) -> str:
    """Why ``level`` is not supported by the edges a corpus run produced.

    Cumulative: L2 must satisfy L1's requirement too, because a language with
    data-flow queries and no working refs queries is not one level up, it is
    two levels of spec with one of them broken.
    """
    from ..extract.spec import LEVEL_EVIDENCE, LEVELS

    for candidate in LEVELS[1:LEVELS.index(level) + 1]:
        required = LEVEL_EVIDENCE[candidate]
        if not present & set(required):
            return (
                f"claims {level} but the corpus produced none of "
                f"{', '.join(required)} -- required for {candidate}"
            )
    return ""


def run_all(*, corpus_root: Path | None = None) -> dict[str, Any]:
    """Run every language's corpus and return the matrix payload."""
    root = corpus_root or CORPUS_ROOT
    languages = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return {
        "languages": {
            lang: run_language(lang, corpus_root=root).as_dict()
            for lang in languages
        },
    }


def write_matrix(*, corpus_root: Path | None = None, path: Path | None = None) -> Path:
    """Generate ``matrix.json``.

    Key-sorted with a trailing newline so regenerating it produces no diff when
    nothing changed -- the file is committed, and a churning artifact stops
    being read.
    """
    target = path or MATRIX_PATH
    payload = run_all(corpus_root=corpus_root)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:  # pragma: no cover - developer entry point
    path = write_matrix()
    payload = json.loads(path.read_text("utf-8"))
    print(f"wrote {path}")
    unsupported: list[str] = []
    for lang, entry in sorted(payload["languages"].items()):
        marks = " ".join(
            f"{name}={entry.get(name)}"
            for name in ("nodes", "calls", "inherits", "implements",
                         "dataflow", "contracts")
        )
        print(f"  {lang:12s} {entry.get('level', '?'):3s} {marks}")
        if entry.get("missing_nodes"):
            print(f"               missing nodes: {entry['missing_nodes']}")
        if entry.get("level_unsupported"):
            print(f"               LEVEL: {entry['level_unsupported']}")
            unsupported.append(lang)

    if unsupported:
        # Non-zero, so CI catches a grammar pinned without a working spec.
        # `pyproject.toml` records why this matters: grammars once sat behind
        # an extra with no spec at all, and Apache Dubbo produced 4,297
        # "classes" matched from `"class " in line`.
        print(f"\nFAIL: unsupported level claims: {', '.join(unsupported)}")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
