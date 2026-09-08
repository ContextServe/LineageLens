"""Reachability: which symbols are actually reached, and by what mechanism.

The question "is this dead code?" was previously answered by "does any relation
target it?". On a real project that was wrong 71% of the time, because it treated
a graph of call edges as a model of reachability, which Python is not. A Pydantic
request model referenced only from a route signature, a dependency handed to
Depends(), a base class, a name in __all__ -- all live, none called.

This module answers the question properly: walk outward from entry points over
every edge kind, and record *why* each symbol was reached. The mechanism is the
important part. A verdict an agent cannot audit is a verdict it should not act
on, so every reachable symbol carries the specific rule that saved it and the
trust tier of that rule.

Two rules cannot be expressed as plain edges and are applied when a symbol is
dequeued:

* a reachable class keeps its dunder methods, because the language invokes them
  with no call site naming them. Critically it does *not* keep its ordinary
  methods -- that would destroy dead-method detection and make the whole feature
  decorative.
* a reachable base method implies its overrides may be invoked polymorphically,
  which is the *reverse* of the OVERRIDES edge. The forward direction (an
  override being reachable implies its base declaration is) is the edge itself.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal

from .analyzer import MODULE_SCOPE_NAME
from .config import ProjectConfig
from .model import CodeGraph, Evidence, Symbol
from .queries import is_test_path

Verdict = Literal[
    "alive",
    "dynamic_only",
    "test_only",
    "public_api",
    "probably_dead",
    "dead",
]

#: Dunder methods the interpreter invokes without any call site naming them.
IMPLICIT_DUNDERS = frozenset(
    {
        "__init__", "__new__", "__post_init__", "__del__",
        "__enter__", "__exit__", "__aenter__", "__aexit__",
        "__call__", "__getattr__", "__setattr__", "__delattr__",
        "__getitem__", "__setitem__", "__delitem__", "__contains__",
        "__iter__", "__next__", "__aiter__", "__anext__",
        "__len__", "__bool__", "__repr__", "__str__", "__format__",
        "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__",
        "__init_subclass__", "__set_name__", "__class_getitem__",
        "__enter_async__", "__reduce__", "__copy__", "__deepcopy__",
    }
)

#: Rescue mechanism per relation kind, when a symbol is reached by that edge.
_MECHANISM_BY_KIND = {
    "CALLS": "static_call",
    "AWAIT_CALLS": "static_call",
    "CREATES_TASK": "static_call",
    "REFERENCES": "passed_as_value",
    "REFERENCES_STRING": "string_reference",
    "ANNOTATES": "type_annotation",
    "INHERITS": "base_class",
    "OVERRIDES": "override_declaration",
    "DECORATES": "decorator",
    "EXPORTS": "dunder_all_export",
    "IMPORTS": "module_scope",
    "USES_FIXTURE": "pytest_fixture_name",
}

#: Mechanism names that are never a syntactic fact, whatever the edge says. Used
#: for the implicit rules, which have no relation to take a tier from.
HEURISTIC_MECHANISMS = frozenset(
    {
        "jedi_inference",
        "jedi_ambiguous",
        "polymorphic_override",
        "pytest_fixture_name",
        "string_reference",
        "mro_name_match",
        "name_collision_unresolved_call",
        "local_type_inference_construction",
        "local_type_inference_factory",
        "local_type_inference_attribute",
        "unresolved_dynamic_dispatch",
    }
)


@dataclass(frozen=True)
class RescueMechanism:
    """Why a symbol is considered reached."""

    name: str
    evidence: Evidence
    detail: str
    via_symbol: str | None = None


@dataclass(frozen=True)
class DeadCodeCandidate:
    """A verdict on one symbol, with the reason attached.

    ``scope`` is deliberately separate from ``verdict``: dead code in a test file
    is a different triage decision from dead code that ships.
    """

    symbol: Symbol
    verdict: str
    scope: str
    rescue: RescueMechanism | None
    reason: str

    @property
    def is_candidate(self) -> bool:
        """Whether this warrants human attention."""
        return self.verdict in ("dead", "probably_dead", "test_only")


@dataclass
class ReachabilityResult:
    """Verdicts for every symbol in one graph."""

    verdicts: dict[str, DeadCodeCandidate] = field(default_factory=dict)
    roots: dict[str, list[str]] = field(default_factory=dict)

    def all_symbols(self) -> list[DeadCodeCandidate]:
        return list(self.verdicts.values())

    def candidates(self) -> list[DeadCodeCandidate]:
        """Symbols warranting attention, worst first."""
        order = {"dead": 0, "probably_dead": 1, "test_only": 2}
        return sorted(
            (c for c in self.verdicts.values() if c.is_candidate),
            key=lambda c: (order.get(c.verdict, 9), c.symbol.id),
        )

    def by_verdict(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for candidate in self.verdicts.values():
            counts[candidate.verdict] += 1
        return dict(counts)

    def explain(self, symbol_id: str) -> DeadCodeCandidate | None:
        return self.verdicts.get(symbol_id)


#: Preference order when several mechanisms reach the same symbol, most
#: informative first. Order matters for more than tidiness: a re-exported symbol
#: is always *also* imported, so without an explicit preference the reported
#: reason would depend on relation ordering rather than on what a reader needs.
#: The question being answered is "what is the strongest reason not to delete
#: this?", so a declared public surface outranks an internal call.
MECHANISM_PRIORITY = (
    "pragma_keep",
    "abstract_declaration",
    "dunder_all_export",
    "static_call",
    "implicit_dunder",
    "base_class",
    "type_annotation",
    "decorator",
    "passed_as_value",
    "module_scope",
    # name-based, below every syntactic fact
    "polymorphic_override",
    "pytest_fixture_name",
    "jedi_inference",
    "string_reference",
    "local_type_inference_construction",
    "local_type_inference_factory",
    "local_type_inference_attribute",
    "unresolved_dynamic_dispatch",
)

_PRIORITY_INDEX = {name: position for position, name in enumerate(MECHANISM_PRIORITY)}


def _rank(mechanism: str) -> int:
    """Sort key for a mechanism. Entry points always win; unknowns sort last."""
    if mechanism.startswith("entry_point:"):
        return -1
    return _PRIORITY_INDEX.get(mechanism, len(MECHANISM_PRIORITY))


@dataclass
class _Graph:
    """Adjacency and structural relationships needed by the walk."""

    outgoing: dict[str, list]
    incoming: dict[str, list]
    dunders_of: dict[str, list[str]]
    children_of: dict[str, list[str]]
    overrides_of: dict[str, list[str]]
    overridden_by: dict[str, list[str]]

    @classmethod
    def build(cls, graph: CodeGraph) -> _Graph:
        outgoing: dict[str, list] = defaultdict(list)
        incoming: dict[str, list] = defaultdict(list)
        overrides_of: dict[str, list[str]] = defaultdict(list)
        overridden_by: dict[str, list[str]] = defaultdict(list)
        children_of: dict[str, list[str]] = defaultdict(list)
        for relation in graph.relations:
            outgoing[relation.source].append(relation)
            incoming[relation.target].append(relation)
            if relation.kind == "OVERRIDES":
                overrides_of[relation.target].append(relation.source)
                overridden_by[relation.source].append(relation.target)

        dunders_of: dict[str, list[str]] = defaultdict(list)
        for symbol in graph.symbols.values():
            if symbol.parent:
                children_of[symbol.parent].append(symbol.id)
                if symbol.name in IMPLICIT_DUNDERS:
                    parent = graph.symbols.get(symbol.parent)
                    if parent is not None and parent.kind == "class":
                        dunders_of[symbol.parent].append(symbol.id)

        return cls(
            outgoing=dict(outgoing),
            incoming=dict(incoming),
            dunders_of=dict(dunders_of),
            children_of=dict(children_of),
            overrides_of=dict(overrides_of),
            overridden_by=dict(overridden_by),
        )


def _walk(graph: CodeGraph, adjacency: _Graph, seeds: list[str]) -> set[str]:
    """Symbols reachable from ``seeds`` over every edge kind, plus implicit rules."""
    seen = {seed for seed in seeds if seed in graph.symbols}
    queue = deque(seen)
    while queue:
        current = queue.popleft()

        targets = [relation.target for relation in adjacency.outgoing.get(current, ())]
        current_sym = graph.symbols[current]
        if current_sym.kind == "class":
            targets.extend(adjacency.dunders_of.get(current, ()))
        elif current_sym.kind in ("function", "method"):
            targets.extend(adjacency.children_of.get(current, ()))
        targets.extend(adjacency.overrides_of.get(current, ()))

        for target in targets:
            if target in graph.symbols and target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def compute_reachability(
    graph: CodeGraph, config: ProjectConfig | None = None
) -> ReachabilityResult:
    """Walk outward from entry points and classify every symbol.

    Runs in O(V+E). Reachability is computed first, then each reached symbol's
    reason is chosen by :data:`MECHANISM_PRIORITY` among every mechanism that
    actually applies -- so the reported reason does not depend on traversal order.
    """
    config = config or ProjectConfig()
    module_scope_roots = config.analysis.module_scope_roots

    adjacency = _Graph.build(graph)
    roots = _collect_roots(graph, module_scope_roots)

    production_seeds = [
        symbol_id
        for symbol_id in roots
        if not _is_test_symbol(graph.symbols[symbol_id], config)
    ]
    reachable = _walk(graph, adjacency, list(roots))
    from_production = _walk(graph, adjacency, production_seeds)

    return _classify(graph, config, adjacency, roots, reachable, from_production)


def _collect_roots(graph: CodeGraph, module_scope_roots: str) -> dict[str, tuple[str, str]]:
    """Entry points, plus module-scope nodes depending on the configured mode.

    ``module_scope_roots="all"`` treats every module body as executing, which
    over-approximates: a module body only runs if something imports it. The
    stricter ``imports_only`` mode seeds only modules that are themselves entry
    points and propagates through IMPORTS edges. ``all`` is the default on
    purpose -- over-approximating costs recall, but it never fabricates a "this
    is dead, delete it", and false confidence is the failure mode that matters.
    """
    roots: dict[str, tuple[str, str]] = {}

    for symbol in graph.symbols.values():
        if symbol.entry_point_kinds:
            kind = symbol.entry_point_kinds[0]
            if kind == "pragma_keep":
                roots[symbol.id] = (
                    "pragma_keep",
                    "marked `# lineagelens: keep`; reachable in a way static analysis cannot see",
                )
            else:
                roots[symbol.id] = (
                    f"entry_point:{kind}",
                    f"invoked from outside the project as {kind}",
                )
        elif symbol.is_abstract:
            roots[symbol.id] = (
                "abstract_declaration",
                "abstract declaration; implementations are reached through the base type",
            )

    if module_scope_roots == "all":
        for symbol in graph.symbols.values():
            if symbol.kind == "module_scope" and symbol.id not in roots:
                roots[symbol.id] = ("module_scope", "module body; executes on import")

    if module_scope_roots == "imports_only":
        seeded = {
            symbol.parent
            for symbol in graph.symbols.values()
            if symbol.entry_point_kinds and symbol.parent
        }
        for symbol in graph.symbols.values():
            if symbol.kind != "module_scope" or symbol.id in roots:
                continue
            if symbol.module in seeded or symbol.entry_point_kinds:
                roots[symbol.id] = (
                    "module_scope",
                    "module body of a module containing an entry point",
                )

    return roots


def _is_test_symbol(symbol: Symbol, config: ProjectConfig) -> bool:
    return is_test_path(symbol.file, test_roots=config.test_roots)


@dataclass(frozen=True)
class _Option:
    """One reason a symbol is reachable, with the confidence of that reason."""

    mechanism: str
    tier: str
    detail: str
    via: str | None

    @property
    def sort_key(self) -> tuple[int, int]:
        # Confidence dominates: a syntactic fact always beats a name match, and
        # only then does the kind of mechanism break the tie.
        return (0 if self.tier == "deterministic_fact" else 1, _rank(self.mechanism))


def _mechanisms_for(
    symbol_id: str,
    graph: CodeGraph,
    adjacency: _Graph,
    roots: dict[str, tuple[str, str]],
    reachable: set[str],
) -> list[_Option]:
    """Every reason this symbol is reachable.

    The tier of an edge-derived reason comes from the *relation*, not from a
    lookup on the mechanism name. Those are different questions: ``passed_as_value``
    describes how the symbol was referenced, while the tier describes how sure we
    are the reference points here. A name handed to a registry, where the
    registry object's type was inferred from its constructor, is a real reference
    at heuristic confidence -- reporting it as a fact would overstate the case.
    """
    found: list[_Option] = []

    if symbol_id in roots:
        mechanism, detail = roots[symbol_id]
        found.append(_Option(mechanism, "deterministic_fact", detail, None))

    for relation in adjacency.incoming.get(symbol_id, ()):
        if relation.source not in reachable:
            continue  # a dead symbol's outgoing edges rescue nothing
        mechanism = _MECHANISM_BY_KIND.get(relation.kind, "static_call")
        tier = relation.resolution_evidence.tier
        if (
            relation.kind in ("CALLS", "AWAIT_CALLS", "CREATES_TASK")
            and tier != "deterministic_fact"
        ):
            # For a call, *how* it was resolved is the audit-relevant fact, so
            # surface the resolution label itself rather than a generic name.
            mechanism = relation.resolution_evidence.label
        found.append(
            _Option(mechanism, tier, f"{relation.kind} from {relation.source}", relation.source)
        )

    symbol = graph.symbols[symbol_id]
    parent = graph.symbols.get(symbol.parent or "")
    if (
        parent is not None
        and parent.kind == "class"
        and parent.id in reachable
        and symbol.name in IMPLICIT_DUNDERS
    ):
        found.append(
            _Option(
                "implicit_dunder",
                "deterministic_fact",
                f"invoked implicitly by the language on {parent.name}",
                parent.id,
            )
        )

    if (
        parent is not None
        and parent.kind in ("function", "method")
        and parent.id in reachable
    ):
        found.append(
            _Option(
                "inner_scope_definition",
                "deterministic_fact",
                f"defined inside reachable scope {parent.name}",
                parent.id,
            )
        )

    for base in adjacency.overridden_by.get(symbol_id, ()):
        if base in reachable:
            found.append(
                _Option(
                    "polymorphic_override",
                    "deterministic_heuristic",
                    f"overrides {base}, which is reachable, so may be invoked polymorphically",
                    base,
                )
            )

    return found


def _classify(
    graph: CodeGraph,
    config: ProjectConfig,
    adjacency: _Graph,
    roots: dict[str, tuple[str, str]],
    reachable: set[str],
    from_production: set[str],
) -> ReachabilityResult:
    """Turn reachability into verdicts, each naming the mechanism that decided it."""
    exported = {
        relation.target for relation in graph.relations if relation.kind == "EXPORTS"
    }

    # Bare names of call targets we could not resolve. A symbol with that name may
    # be the real destination -- a weak hint, enough to say "probably" rather than
    # to assert deadness.
    unresolved_names: set[str] = set()
    for relation in graph.relations:
        if relation.resolution == "external_or_dynamic" and relation.target.startswith("external:"):
            tail = relation.target[len("external:") :].rsplit(".", 1)[-1]
            if tail and tail != "<unresolved>":
                unresolved_names.add(tail)

    result = ReachabilityResult(roots={k: [v[0]] for k, v in roots.items()})

    for symbol_id, symbol in graph.symbols.items():
        scope = "test" if _is_test_symbol(symbol, config) else "source"

        if symbol_id in reachable:
            options = _mechanisms_for(symbol_id, graph, adjacency, roots, reachable)
            best = min(options, key=lambda option: option.sort_key)
            mechanism, tier = best.mechanism, best.tier
            rescue = RescueMechanism(
                name=mechanism,
                evidence=Evidence(tier=tier, label=mechanism),
                detail=best.detail,
                via_symbol=best.via,
            )
            if scope == "source" and symbol_id not in from_production:
                verdict = "test_only"
                reason = (
                    "reachable only from test entry points: deleting it would break "
                    "the suite, but nothing that ships uses it"
                )
            elif tier == "deterministic_fact":
                verdict = "alive"
                reason = f"reached from an entry point via {mechanism}"
            else:
                verdict = "dynamic_only"
                reason = (
                    f"reached only via {mechanism}, a name match rather than a "
                    "syntactic fact -- live, but verify before removing"
                )
            result.verdicts[symbol_id] = DeadCodeCandidate(
                symbol=symbol, verdict=verdict, scope=scope, rescue=rescue, reason=reason
            )
            continue

        if symbol_id in exported:
            result.verdicts[symbol_id] = DeadCodeCandidate(
                symbol=symbol,
                verdict="public_api",
                scope=scope,
                rescue=RescueMechanism(
                    name="dunder_all_export",
                    evidence=Evidence(tier="deterministic_fact", label="dunder_all_export"),
                    detail="listed in a package __all__; consumers are outside this repo",
                ),
                reason="unreferenced in-repo but part of the declared public surface",
            )
            continue

        if symbol.kind in ("function", "method") and symbol.name in unresolved_names:
            result.verdicts[symbol_id] = DeadCodeCandidate(
                symbol=symbol,
                verdict="probably_dead",
                scope=scope,
                rescue=RescueMechanism(
                    name="name_collision_unresolved_call",
                    evidence=Evidence(
                        tier="deterministic_heuristic", label="name_collision_unresolved_call"
                    ),
                    detail=(
                        f"an unresolved call site elsewhere targets something named "
                        f"'{symbol.name}'; it may or may not be this one"
                    ),
                ),
                reason=(
                    "not reachable from any entry point, but a dynamic call site shares "
                    "its name, so deadness cannot be asserted"
                ),
            )
            continue

        result.verdicts[symbol_id] = DeadCodeCandidate(
            symbol=symbol,
            verdict="dead",
            scope=scope,
            rescue=None,
            reason=(
                "not reachable from any entry point by any modelled mechanism, and no "
                "same-named dynamic call site exists. Static analysis cannot see "
                "reflection or config-driven dispatch: if it is reached that way, mark "
                "it `# lineagelens: keep`"
            ),
        )

    return result


def module_scope_ids(graph: CodeGraph) -> set[str]:
    """Ids of the synthetic module-scope nodes."""
    return {
        symbol.id
        for symbol in graph.symbols.values()
        if symbol.name == MODULE_SCOPE_NAME
    }


__all__ = [
    "IMPLICIT_DUNDERS",
    "DeadCodeCandidate",
    "ReachabilityResult",
    "RescueMechanism",
    "Verdict",
    "compute_reachability",
    "module_scope_ids",
]
