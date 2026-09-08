"""The only component that creates edges (issue #51 §4).

Everything an extractor produced is an *observation*: a name, a span, sometimes
a receiver. This module turns those into edges, or -- when it cannot -- into
retained ``unresolved_refs`` rows and ``boundaries`` records.

The rule that makes the graph trustworthy
-----------------------------------------

**Never pick arbitrarily.** When several candidates match, the resolver records
an ambiguity with the full candidate set and emits no edge. That is the single
behavioural difference from schema 3, which matched a reference by its bare
trailing name, took ``possible_targets[0]``, and discarded anything left over.
The measured consequence on Apache Dubbo: 2,593 edges all reporting
``resolution=resolved``, every one of them with a dangling source, and a graph
with zero traversable paths that reported success.

An ambiguity is a useful answer. A wrong edge is not.

Resolution order
----------------

For each reference, cheapest and most certain first:

1. **Self-receiver** -- ``self.save()`` resolves against the enclosing type and
   its resolved bases. Certain, and needs no type inference.
2. **Lexical scope** -- an unqualified name resolves outward through locals,
   parameters, type members, module members. This is what scoping means.
3. **Typed receiver** -- ``repo.save()`` where ``repo`` is a parameter declared
   ``Repository`` resolves against that type. Deterministic, because the type
   was written down.
4. **Import** -- a name brought in by this file's import list.
5. **Tier B oracle** -- ask a language toolchain (§7.2). The only step that can
   resolve an overload or a structurally-satisfied interface.
6. **Give up honestly** -- record the candidate set as ambiguous, or record it
   as external if there were none.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..core import (
    Boundary,
    BoundaryKind,
    Contract,
    Coverage,
    DataflowStatus,
    Edge,
    EdgeKind,
    Evidence,
    Node,
    NodeFlags,
    NodeKind,
    Observation,
    RefKind,
    RefStatus,
    Resolution,
    Span,
    UnresolvedRef,
)
from .index import SELF_RECEIVERS, TARGET_KINDS, TYPE_KINDS, SymbolIndex
from .oracles import OracleRegistry

logger = logging.getLogger(__name__)

#: How each observed reference kind becomes an edge kind. INHERIT is
#: provisional: C# writes base classes and interfaces in one list with no
#: syntactic distinction, so the resolver reclassifies to IMPLEMENTS once the
#: target's kind is known (see :meth:`Resolver._edge_kind_for`).
REF_TO_EDGE: dict[RefKind, EdgeKind] = {
    RefKind.CALL: EdgeKind.CALLS,
    RefKind.TYPE: EdgeKind.HAS_TYPE,
    RefKind.READ: EdgeKind.READS,
    RefKind.WRITE: EdgeKind.WRITES,
    RefKind.IMPORT: EdgeKind.IMPORTS,
    RefKind.INHERIT: EdgeKind.INHERITS,
    RefKind.IMPLEMENT: EdgeKind.IMPLEMENTS,
    RefKind.DECORATE: EdgeKind.DECORATES,
    RefKind.INSTANTIATE: EdgeKind.INSTANTIATES,
    RefKind.THROW: EdgeKind.THROWS,
}

#: Reference kinds that carry data flow, so a resolved one may also yield
#: PARAM_BINDS / RETURNS (§9).
DATA_REFS = frozenset({RefKind.READ, RefKind.WRITE})

#: Cap on candidates stored on an unresolved row. The count is what a caller
#: acts on; a hundred ids is not more informative than ten and inflates the
#: store measurably on a large repository.
MAX_RECORDED_CANDIDATES = 16

#: Above this, an ambiguity is too broad to be a useful boundary record. See
#: the comment at the emission site.
MAX_BOUNDARY_CANDIDATES = 8


@dataclass(slots=True)
class ResolveResult:
    """Everything one resolve pass produced."""

    edges: list[Edge] = field(default_factory=list)
    unresolved: list[UnresolvedRef] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    coverage: dict[str, Coverage] = field(default_factory=dict)

    def extend(self, other: ResolveResult) -> None:
        self.edges.extend(other.edges)
        self.unresolved.extend(other.unresolved)
        self.boundaries.extend(other.boundaries)
        self.contracts.extend(other.contracts)
        self.coverage.update(other.coverage)


class Resolver:
    """Turns observations into edges, ambiguities and boundaries."""

    def __init__(
        self,
        index: SymbolIndex,
        *,
        oracles: OracleRegistry | None = None,
        provenance: str = "tier-a-resolver",
    ) -> None:
        self.index = index
        self.oracles = oracles or OracleRegistry()
        self.provenance = provenance

    # ---- entry point ------------------------------------------------------

    def resolve(self, observations: Sequence[Observation]) -> ResolveResult:
        """Resolve every observation against the index.

        Two passes, and the order matters. Structural references
        (inherit/implement/import) go first, because member lookup follows
        resolved bases: resolving ``self.save()`` against an inherited method
        requires the INHERITS edge to exist already. Doing it in one pass would
        make the result depend on file order, which §11 forbids.
        """
        result = ResolveResult()

        result.edges.extend(self._contains_edges(observations))

        structural = {RefKind.INHERIT, RefKind.IMPLEMENT, RefKind.IMPORT}
        for phase in (structural, None):
            for observation in observations:
                self._resolve_file(observation, result, only=phase, exclude=structural
                                   if phase is None else None)

        for observation in observations:
            result.coverage[observation.file.path] = self._coverage_for(observation, result)
            result.boundaries.extend(observation.boundaries)
            result.contracts.extend(observation.contracts)

        return result

    def _resolve_file(
        self,
        observation: Observation,
        result: ResolveResult,
        *,
        only: set[RefKind] | None,
        exclude: set[RefKind] | None,
    ) -> None:
        imports = self._import_map(observation)
        for ref in observation.refs:
            if only is not None and ref.ref_kind not in only:
                continue
            if exclude is not None and ref.ref_kind in exclude:
                continue
            self._resolve_ref(ref, imports, result)

    # ---- structural edges -------------------------------------------------

    def _contains_edges(self, observations: Sequence[Observation]) -> list[Edge]:
        """Materialise CONTAINS from the extractor's ``parent_id``.

        Lexical nesting is read straight off the parse tree, so it needs no
        resolution -- but it is still an edge, and edges are created here. The
        alternative, deriving nesting again in the query layer, would mean two
        places that could disagree about the shape of the tree.
        """
        edges: list[Edge] = []
        evidence = Evidence.fact("lexical_containment")
        for observation in observations:
            for node in observation.nodes:
                if not node.parent_id or self.index.get(node.parent_id) is None:
                    continue
                edges.append(Edge(
                    src=node.parent_id,
                    dst=node.id,
                    kind=EdgeKind.CONTAINS,
                    evidence=evidence,
                    resolution=Resolution.EXACT,
                    provenance="tier-a-spec",
                    span=node.span,
                    file_path=node.file_path,
                ))
        return edges

    # ---- one reference ----------------------------------------------------

    def _resolve_ref(
        self, ref: UnresolvedRef, imports: dict[str, list[Node]], result: ResolveResult
    ) -> None:
        edge_kind = REF_TO_EDGE.get(ref.ref_kind)
        if edge_kind is None:
            result.unresolved.append(_as_unresolved(
                ref, RefStatus.FAILED, f"no edge kind for ref kind {ref.ref_kind}"
            ))
            return

        candidates, how = self._candidates(ref, edge_kind, imports)

        if len(candidates) == 1:
            target = candidates[0]
            actual_kind = self._edge_kind_for(edge_kind, target)
            result.edges.append(ref.resolved_to(
                target=target.id,
                kind=actual_kind,
                evidence=Evidence.fact(how) if how.startswith("lexical") or
                         how in ("self_receiver", "declared_receiver_type", "tier_b")
                         else Evidence.heuristic(how),
                resolution=Resolution.EXACT if how in (
                    "self_receiver", "declared_receiver_type", "lexical_scope", "tier_b"
                ) else Resolution.INFERRED,
                provenance=self.provenance if how != "tier_b" else "tier-b-oracle",
            ))
            self._after_edge(ref, actual_kind, target, result)
            return

        if len(candidates) > 1:
            # Several plausible targets. Record every one and emit no edge --
            # this is the branch schema 3 replaced with `possible_targets[0]`.
            ids = tuple(sorted(n.id for n in candidates))
            result.unresolved.append(_as_unresolved(
                ref, RefStatus.AMBIGUOUS,
                f"{len(candidates)} candidates via {how}; no edge emitted",
                candidates=ids[:MAX_RECORDED_CANDIDATES],
            ))
            # A boundary is only useful if a reader could act on it. A handful of
            # candidates is a real dispatch decision worth surfacing; a hundred
            # means the lookup was too broad to say anything, and recording it
            # would bury the informative boundaries in noise. The count still
            # reaches the caller through the unresolved row either way.
            if len(candidates) <= MAX_BOUNDARY_CANDIDATES:
                result.boundaries.append(Boundary(
                    node_id=ref.from_node,
                    kind=BoundaryKind.DYNAMIC_DISPATCH,
                    detail=(
                        f"{ref.ref_text!r} at {ref.file_path}:{ref.span.start_line} "
                        f"has {len(candidates)} candidates"
                    ),
                    span=ref.span,
                    candidates=ids,
                ))
            return

        result.unresolved.append(_as_unresolved(
            ref, RefStatus.EXTERNAL,
            "out of lexical scope" if how == "out_of_scope"
            else "no candidate in the indexed repository",
        ))

    def _candidates(
        self, ref: UnresolvedRef, edge_kind: EdgeKind, imports: dict[str, list[Node]]
    ) -> tuple[list[Node], str]:
        """Candidate targets, narrowed by plausible kind, plus how they were found."""
        allowed = TARGET_KINDS.get(edge_kind)

        def narrow(nodes: Iterable[Node]) -> list[Node]:
            found = list(nodes)
            if allowed is None:
                return found
            typed = [n for n in found if n.kind in allowed]
            # If narrowing removes everything, the kind filter is wrong for this
            # language rather than the candidates being invalid -- keep them and
            # let ambiguity handling decide.
            return typed or found

        name = _trailing_name(ref.ref_text)
        receiver = ref.receiver_hint

        # 1. self.x -- resolve against the enclosing type.
        if receiver and receiver in SELF_RECEIVERS:
            owner = self.index.enclosing_type(ref.from_node)
            if owner is not None:
                found = narrow(self.index.members(owner.id, name))
                if found:
                    return found, "self_receiver"

        # 2. Unqualified name -- lexical scope.
        if not receiver:
            found = narrow(self.index.in_scope(ref.from_node, name))
            if found:
                return found, "lexical_scope"
            found = narrow(self.index.sibling_module_symbol(ref.from_node, name))
            if found:
                return found, "lexical_scope"

        # 3. Typed receiver -- `repo.save()` where repo is declared Repository.
        if receiver:
            for owner in self._receiver_types(ref.from_node, receiver):
                found = narrow(self.index.members(owner.id, name))
                if found:
                    return found, "declared_receiver_type"

        # 4. Imports.
        for key in (ref.ref_text, name, receiver or ""):
            if key and key in imports:
                found = narrow(imports[key])
                if found:
                    return found, "import"

        # 5. Tier B oracle.
        resolved = self.oracles.resolve(ref)
        if resolved is not None and not resolved.is_external:
            found = narrow(self.index.by_qualified_name(resolved.qualified_name))
            if len(found) > 1 and resolved.signature_hash:
                exact = [n for n in found if n.signature_hash == resolved.signature_hash]
                if exact:
                    found = exact
            if found:
                return found, "tier_b"

        # 6. Project-wide bare-name lookup, as a last resort.
        #
        #    Deliberately *not* applied to data references. A read or write of a
        #    name that is not in lexical scope is out of scope -- it is not a
        #    hundred-way ambiguity with every same-named local in the repository.
        #    Applying it indiscriminately produced 130 candidates for one read of
        #    `graph`, spanning Python locals and Java parameters, which is noise
        #    rather than information.
        if ref.ref_kind in DATA_REFS:
            return [], "out_of_scope"

        #    Candidates are also confined to the reference's own language. A
        #    Python call cannot target a Java method, and letting it try is how a
        #    project-wide name table manufactures cross-language edges that do
        #    not exist. Genuine cross-language links go through contract nodes
        #    (§8), never through a shared identifier.
        lang = self._lang_of(ref.from_node)
        pool = [n for n in self.index.by_name(name) if lang is None or n.lang == lang]
        return narrow(pool), "project_wide_name"

    def _lang_of(self, node_id: str) -> str | None:
        node = self.index.get(node_id)
        return node.lang if node is not None else None

    def _receiver_types(self, from_node: str, receiver: str) -> list[Node]:
        """Types a receiver expression could denote, from declared types only.

        Deliberately limited to what was written down: a parameter or field with
        a declared type, or a name that is itself a type (a static call). No
        inference beyond that -- guessing a receiver's type is how a call graph
        acquires edges that are not real.
        """
        base = _trailing_name(receiver)

        owners: list[Node] = []
        for holder in self.index.in_scope(from_node, base):
            if holder.kind in TYPE_KINDS:
                owners.append(holder)
                continue
            declared = holder.type_ref or holder.return_type
            if declared:
                owners.extend(
                    n for n in self.index.by_name(_trailing_name(declared))
                    if n.kind in TYPE_KINDS
                )

        if not owners:
            owners.extend(n for n in self.index.by_name(base) if n.kind in TYPE_KINDS)
        return owners

    def _import_map(self, observation: Observation) -> dict[str, list[Node]]:
        """What this file's import statements bring into scope.

        Built per file from its own IMPORT observations, so a name imported in
        one module is not treated as visible in another -- which is the scoping
        error that makes a project-wide name table produce false edges.
        """
        lang = observation.file.lang
        mapping: dict[str, list[Node]] = defaultdict(list)
        for ref in observation.refs:
            if ref.ref_kind is not RefKind.IMPORT:
                continue
            raw = ref.ref_text.strip("\"'`")
            name = _trailing_name(raw)

            module = self.index.module(raw) or self.index.module(
                raw.replace(".", "/").strip("/")
            )
            if module is not None:
                mapping[name].append(module)
                mapping[raw].append(module)
                continue

            # `from app.repo import Repository` -- the module is the receiver
            # hint and the imported name is what matters.
            #
            # Confined to the importing file's language. Without that, a Python
            # `from .model import Symbol` also matched a Java class and a
            # TypeScript interface of the same name, turning a unique import into
            # a three-way ambiguity. An import statement can only ever bind a
            # symbol from its own language.
            for node in self.index.by_name(name):
                if node.lang == lang:
                    mapping[name].append(node)
        return dict(mapping)

    # ---- follow-on edges --------------------------------------------------

    def _after_edge(
        self, ref: UnresolvedRef, kind: EdgeKind, target: Node, result: ResolveResult
    ) -> None:
        """Edges that only become derivable once a reference has resolved."""
        if kind in (EdgeKind.INHERITS, EdgeKind.IMPLEMENTS):
            owner = self.index.enclosing_type(ref.from_node)
            if owner is not None:
                self.index.add_inheritance(owner.id, target.id)

        if kind is EdgeKind.CALLS:
            result.edges.extend(self._param_binds(ref, target))

    def _param_binds(self, ref: UnresolvedRef, target: Node) -> list[Edge]:
        """Bind each argument at a call site to the callee's parameter (§9).

        Deterministic given a resolved call: argument *N* binds parameter *N*.
        This is the hop that carries a value across a call boundary, and it is
        buildable only because the extractor keeps argument spans and positions.
        Schema 3 computed 3,326 argument lists and persisted none of them, which
        is why parameter binding could not be implemented on top of it.
        """
        args = ref.metadata.get("args") or []
        if not args:
            return []

        params = sorted(
            (n for n in self.index.all_members(target.id) if n.kind is NodeKind.PARAMETER),
            key=lambda n: n.span.start_byte,
        )
        # An implicit receiver occupies parameter 0 in the declaration but not at
        # the call site, so `self.save(x)` would otherwise bind x to `self`.
        if params and params[0].name in SELF_RECEIVERS:
            params = params[1:]
        if not params:
            return []

        edges: list[Edge] = []
        evidence = Evidence.fact("positional_argument_binding")
        for arg in args:
            position = arg.get("index", 0)
            if position >= len(params):
                # More arguments than parameters: varargs, or a mis-resolved
                # overload. Either way, binding beyond the declared list would
                # be invention.
                break
            span = Span(
                start_byte=arg["start_byte"], end_byte=arg["end_byte"],
                start_line=arg.get("line", ref.span.start_line), start_col=0,
                end_line=arg.get("line", ref.span.start_line), end_col=0,
            )
            edges.append(Edge(
                src=ref.from_node,
                dst=params[position].id,
                kind=EdgeKind.PARAM_BINDS,
                evidence=evidence,
                resolution=Resolution.EXACT,
                provenance=self.provenance,
                span=span,
                file_path=ref.file_path,
                metadata={"arg_index": position, "arg_text": arg.get("text", "")},
            ))
        return edges

    def _edge_kind_for(self, kind: EdgeKind, target: Node) -> EdgeKind:
        """Refine an edge kind now that the target's kind is known.

        C# lists base classes and interfaces together with no syntactic
        difference, so the spec can only observe "inherit". Reclassifying here
        is a fact about the target, not a naming convention -- guessing from an
        `I` prefix would be the latter.
        """
        if kind is EdgeKind.INHERITS and target.kind is NodeKind.INTERFACE:
            return EdgeKind.IMPLEMENTS
        if kind is EdgeKind.CALLS and target.kind in TYPE_KINDS:
            return EdgeKind.INSTANTIATES
        return kind

    # ---- accounting -------------------------------------------------------

    def _coverage_for(self, observation: Observation, result: ResolveResult) -> Coverage:
        """Per-file reference accounting (§10.6).

        The totals must balance: every observed reference became an edge or an
        ``unresolved_refs`` row. ``Coverage.__post_init__`` and a CHECK
        constraint both enforce it, which turns "no silent drops" into a
        checked property rather than a claim.
        """
        path = observation.file.path
        node_ids = {n.id for n in observation.nodes}

        exact = inferred = 0
        for edge in result.edges:
            if edge.file_path != path or edge.kind is EdgeKind.CONTAINS:
                continue
            if edge.kind is EdgeKind.PARAM_BINDS:
                continue  # derived, not an observed reference
            if edge.resolution is Resolution.EXACT:
                exact += 1
            else:
                inferred += 1

        unresolved = sum(1 for r in result.unresolved if r.file_path == path)
        boundaries = sum(
            1 for b in result.boundaries
            if b.node_id in node_ids or b.node_id == observation.file.path
        )

        return Coverage(
            file_path=path,
            nodes_found=len(observation.nodes),
            refs_total=exact + inferred + unresolved,
            refs_exact=exact,
            refs_inferred=inferred,
            refs_unresolved=unresolved,
            boundaries_count=boundaries,
            dataflow_status=(
                DataflowStatus.COMPUTED
                if any(r.ref_kind in DATA_REFS for r in observation.refs)
                else DataflowStatus.UNSUPPORTED
            ),
        )


def _as_unresolved(
    ref: UnresolvedRef,
    status: RefStatus,
    reason: str,
    candidates: tuple[str, ...] = (),
) -> UnresolvedRef:
    """Stamp an observation with its terminal state, keeping everything else."""
    from dataclasses import replace

    return replace(ref, status=status, reason=reason,
                   candidates=candidates or ref.candidates)


def _trailing_name(text: str) -> str:
    """Last identifier segment of a possibly-qualified name.

    Used to *look up* candidates, never to decide between them. That distinction
    is the whole point: schema 3 matched on this and then picked the first hit.
    """
    cleaned = text.strip().strip("\"'`")
    for separator in ("::", "->", ".", "/", "\\"):
        if separator in cleaned:
            cleaned = cleaned.rsplit(separator, 1)[-1]
    return cleaned.strip("&*@[]()<> ")


def mark_entry_points(nodes: Iterable[Node], contracts: Iterable[Contract]) -> list[Node]:
    """Set ENTRY_POINT on nodes that expose a contract.

    Denormalised from the EXPOSES edges so the hot "is this an entry point"
    check needs no join. Schema 3 kept this as a list attribute that the SQLite
    writer did not persist, so all 315 entry points read back as zero and every
    reachability root vanished.
    """
    exposed = {c.id for c in contracts}
    updated: list[Node] = []
    for node in nodes:
        if node.id in exposed:
            from dataclasses import replace

            updated.append(replace(node, flags=node.flags | NodeFlags.ENTRY_POINT))
        else:
            updated.append(node)
    return updated
