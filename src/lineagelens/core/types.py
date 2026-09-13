"""The record types that move between extraction, resolution, and the store.

The type boundary here *is* the architectural invariant of issue #51 (§4):

    An extractor may never emit a resolved edge.

An extractor returns an :class:`Observation` -- nodes it saw, and references it
saw, each with a span. It has no way to express an :class:`Edge`, because
producing one requires a target node id and observations do not carry them. Only
the resolver constructs edges.

That is why the fabrication and silent-drop failures of schema 3 cannot recur
here: ``treesitter_analyzer`` matched references by bare trailing name, picked an
arbitrary candidate, and discarded anything left over. Under these types it
could do neither -- unmatched references are the *output format*
(:class:`UnresolvedRef`), not a leftover to be dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .kinds import (
    BoundaryKind,
    ContractKind,
    DataflowStatus,
    EdgeKind,
    EvidenceTier,
    NodeFlags,
    NodeKind,
    ParseStatus,
    RefKind,
    RefStatus,
    Resolution,
    SkipReason,
    Tier,
    Visibility,
)
from .span import Span


@dataclass(frozen=True, slots=True)
class Evidence:
    """Why a fact is believed, and how strongly.

    ``label`` names the specific mechanism (``tree_sitter_ast``,
    ``javac_resolve``, ``route_key_match``) so ``explain()`` can report the
    actual provenance rather than a bare tier. Schema 3 collapsed these into one
    free-text string and then mapped it back with a hand-maintained lookup that
    silently defaulted unknown labels to "heuristic".
    """

    tier: EvidenceTier
    label: str
    confidence: float | None = None  # only meaningful for PROBABILISTIC

    def __post_init__(self) -> None:
        if self.tier is EvidenceTier.PROBABILISTIC and self.confidence is None:
            raise ValueError("probabilistic evidence requires a confidence")
        if self.tier is not EvidenceTier.PROBABILISTIC and self.confidence is not None:
            raise ValueError(f"confidence is meaningless for tier {self.tier}")

    @classmethod
    def fact(cls, label: str) -> Evidence:
        return cls(tier=EvidenceTier.FACT, label=label)

    @classmethod
    def heuristic(cls, label: str) -> Evidence:
        return cls(tier=EvidenceTier.HEURISTIC, label=label)


@dataclass(frozen=True, slots=True)
class Service:
    """A deployment unit (§7.4).

    The dimension that separates "this breaks the build" from "this breaks the
    wire contract with another deployable" -- which are different answers to
    "what does changing this affect", and schema 3 could not tell them apart.
    """

    id: str
    name: str
    root: str
    kind: str                    # maven|gradle|npm|pypi|go|cargo|dotnet|docker|k8s
    manifest_path: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractorRun:
    """Which tool produced facts for a file, and at which tier."""

    name: str
    version: str
    tier: Tier


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One row of the completeness ledger (§10.6).

    Every file the walker saw gets one of these, including files that were
    skipped -- that is the whole point. A negative answer ("nothing calls this")
    is only trustworthy if you can also say which files were never read.
    """

    path: str
    lang: str
    content_hash: str
    size_bytes: int
    parse_status: ParseStatus
    service_id: str | None = None
    parse_errors: tuple[ParseError, ...] = ()
    skip_reason: SkipReason | None = None
    extractors: tuple[ExtractorRun, ...] = ()
    id: int | None = None  # assigned by the store

    def __post_init__(self) -> None:
        if self.parse_status is ParseStatus.SKIPPED and self.skip_reason is None:
            raise ValueError(f"{self.path}: skipped files must record a skip_reason")


@dataclass(frozen=True, slots=True)
class ParseError:
    """A syntax error the grammar reported, retained rather than swallowed."""

    line: int
    col: int
    message: str


@dataclass(frozen=True, slots=True)
class Node:
    """A thing in the graph.

    Unlike schema 3's ``Symbol``, nothing here is optional-by-accident: the store
    persists every field (§16.3). The schema-3 SQLite writer kept 9 of ~25
    attributes, so 315 entry points, 499 signatures and 3,326 argument lists were
    computed on every index and then silently thrown away.
    """

    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    lang: str
    span: Span
    file_path: str
    service_id: str | None = None
    signature: str | None = None
    signature_hash: str = ""
    docstring: str | None = None
    return_type: str | None = None
    type_ref: str | None = None      # field/parameter/variable/constant type
    visibility: Visibility | None = None
    flags: NodeFlags = NodeFlags.NONE
    type_params: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    parent_id: str | None = None     # denormalised CONTAINS parent, for fast ancestry
    file_id: int | None = None       # assigned by the store

    @property
    def is_callable(self) -> bool:
        return self.kind in (
            NodeKind.FUNCTION, NodeKind.METHOD,
            NodeKind.CONSTRUCTOR, NodeKind.PROPERTY,
        )

    @property
    def is_type(self) -> bool:
        return self.kind in (
            NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM,
            NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.TYPE_ALIAS,
            NodeKind.ANNOTATION,
        )

    @property
    def is_value(self) -> bool:
        """Nodes a data-flow edge can terminate on (§9)."""
        return self.kind in (
            NodeKind.FIELD, NodeKind.PARAMETER,
            NodeKind.VARIABLE, NodeKind.CONSTANT,
        )

    def has(self, flag: NodeFlags) -> bool:
        return bool(self.flags & flag)


@dataclass(frozen=True, slots=True)
class Contract:
    """A cross-framework binding point (§8.1).

    Modelled as a node rather than a direct producer->consumer edge, for four
    reasons that all turned out to matter: the join key stays visible in an
    answer; many-to-many falls out naturally; the heuristic lives on its own
    edges and can be switched off independently; and a contract with consumers
    but no exposer is a *finding* (external dependency, or a bug) rather than an
    invisible gap.
    """

    id: str
    kind: ContractKind
    key: str             # as written, for display
    normalised_key: str  # what the join happens on
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_node(self) -> Node:
        """Contracts live in the ``nodes`` table so one traversal walks them.

        Keeping them in a side table would mean the query layer needed a special
        case to cross a framework boundary; as nodes, a cross-service path is
        just a two-hop walk.
        """
        return Node(
            id=self.id,
            kind=NodeKind.CONTRACT,
            name=self.key,
            qualified_name=f"contract/{self.kind}/{self.normalised_key}",
            lang="*",  # a contract is language-agnostic; that is its purpose
            span=Span.at_line(0),
            file_path="",
            signature=self.normalised_key,
        )


@dataclass(frozen=True, slots=True)
class Edge:
    """A resolved relationship. Only the resolver may construct one.

    ``span`` is where the edge is *written* -- the call site, the assignment, the
    annotation. That is what makes ``impact_of("file.py:412")`` possible without
    exploding the graph into one node per statement: a line maps directly to the
    operations on it.
    """

    src: str
    dst: str
    kind: EdgeKind
    evidence: Evidence
    resolution: Resolution
    provenance: str                 # extractor / oracle / adapter that produced it
    span: Span | None = None
    file_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    file_id: int | None = None      # assigned by the store

    @property
    def identity(self) -> tuple[str, str, str, int, int]:
        """Dedupe key, matching the store's unique index.

        Includes the span: two calls to the same target from the same function on
        different lines are genuinely two edges, and collapsing them would lose
        the line-level precision §10.4 depends on.
        """
        return (
            self.src, self.dst, self.kind.value,
            self.span.start_byte if self.span else -1,
            self.span.end_byte if self.span else -1,
        )


@dataclass(frozen=True, slots=True)
class UnresolvedRef:
    """A reference an extractor saw but could not -- and must not -- resolve.

    This is an extractor's normal output, not an error path. Retaining these is
    what lets the store answer "how much of this file did we actually
    understand", and it is the difference between schema 3's Dubbo graph
    (claiming ``resolved`` on 2,593 of 2,593 edges after discarding every
    failure) and an honest one.
    """

    from_node: str
    ref_text: str
    ref_kind: RefKind
    span: Span
    file_path: str
    receiver_hint: str | None = None      # e.g. inferred type of the call receiver
    candidates: tuple[str, ...] = ()      # node ids, when several match
    status: RefStatus = RefStatus.PENDING
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    file_id: int | None = None

    def resolved_to(
        self,
        target: str,
        kind: EdgeKind,
        evidence: Evidence,
        resolution: Resolution,
        provenance: str,
    ) -> Edge:
        """Promote this observation into an edge. The resolver's only path to one."""
        return Edge(
            src=self.from_node,
            dst=target,
            kind=kind,
            evidence=evidence,
            resolution=resolution,
            provenance=provenance,
            span=self.span,
            file_path=self.file_path,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True, slots=True)
class Boundary:
    """Where analysis provably stops (§9.1).

    Written *instead of* an edge, and surfaced on every query that crosses it.
    An enumerated boundary with a candidate set is strictly more useful than
    either a guess or silence: the caller learns exactly what is unknown and how
    wide the ambiguity is.
    """

    node_id: str
    kind: BoundaryKind
    detail: str | None = None
    span: Span | None = None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Coverage:
    """Per-file resolution accounting.

    ``refs_total == refs_exact + refs_inferred + refs_unresolved`` is asserted by
    the store on write (§16.5). That invariant is what makes "zero silent drops"
    a checked property rather than a claim.
    """

    file_path: str
    nodes_found: int
    refs_total: int
    refs_exact: int
    refs_inferred: int
    refs_unresolved: int
    boundaries_count: int
    dataflow_status: DataflowStatus

    def __post_init__(self) -> None:
        accounted = self.refs_exact + self.refs_inferred + self.refs_unresolved
        if accounted != self.refs_total:
            raise ValueError(
                f"{self.file_path}: refs unaccounted for -- "
                f"total={self.refs_total} but exact+inferred+unresolved={accounted}. "
                "Every reference must land in exactly one bucket."
            )


@dataclass(slots=True)
class Observation:
    """Everything one extractor learned from one file.

    Note what is absent: there is no ``edges`` field, and no way to add one. An
    extractor cannot express a resolved relationship, which is the invariant
    stated in the module docstring enforced by the type system rather than by
    convention or review.
    """

    file: FileRecord
    nodes: list[Node] = field(default_factory=list)
    refs: list[UnresolvedRef] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)

    def extend(self, other: Observation) -> None:
        """Merge another extractor's observations for the same file.

        Used when Tier A and a Tier B oracle both contribute to one file. Node
        collisions are expected and benign -- both tiers minting the same id for
        the same symbol is exactly what §6 is designed to guarantee -- so
        dedupe happens in the store, not here.
        """
        if other.file.path != self.file.path:
            raise ValueError(
                f"cannot merge observations across files: {self.file.path} != {other.file.path}"
            )
        self.nodes.extend(other.nodes)
        self.refs.extend(other.refs)
        self.boundaries.extend(other.boundaries)
        self.contracts.extend(other.contracts)


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A Tier B oracle's answer about one reference (§7.2).

    The oracle's entire vocabulary. It cannot mint node ids, write edges, or
    describe structure -- it answers "what does this name point at" and "what
    type is this expression", and nothing else. Keeping the interface this narrow
    is what makes six language toolchains tractable.
    """

    qualified_name: str
    kind: NodeKind | None = None
    signature_hash: str = ""
    evidence: Evidence = field(default_factory=lambda: Evidence.fact("tier_b_resolve"))
    resolution: Resolution = Resolution.EXACT
    is_external: bool = False
    candidates: tuple[str, ...] = ()


def dumps(value: Any) -> str | None:
    """Compact, key-sorted JSON for the store's TEXT columns.

    Sorted keys are not cosmetic: ``build_digest`` (§11) hashes stored rows, so
    dict iteration order leaking into a column would make two builds of the same
    commit differ and break ``--verify-determinism``.
    """
    if value is None or value == () or value == [] or value == {}:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def loads(raw: str | None) -> Any:
    """Inverse of :func:`dumps`; ``None`` and empty text decode to ``None``."""
    if not raw:
        return None
    return json.loads(raw)
