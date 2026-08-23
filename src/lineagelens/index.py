"""Cached, indexed views over a CodeGraph.

``CodeGraph`` stores relations as a flat list, so every caller/callee lookup is a
linear scan. That is fine once, but the query layer does it per symbol: computing
codebase metrics walked the whole relation list for every symbol, which is O(V*E).
And every REST endpoint re-read and re-parsed graph.json from disk on each
request -- on a real project that is a 10MB JSON parse per HTTP call.

:class:`GraphIndex` builds each view once, lazily, and :func:`load_index` caches
the whole thing against the mtime of the files it was built from.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from .config import ProjectConfig
from .model import CodeGraph, Relation, Symbol
from .queries import load_graph, load_report
from .report import AnalysisReport

# Relation kinds that represent an actual transfer of control, as opposed to a
# reference. Kept separate so call-depth metrics stay about calls.
CALL_KINDS = frozenset({"CALLS", "AWAIT_CALLS", "CREATES_TASK"})


@dataclass
class GraphIndex:
    """Lazily-built lookup tables over one graph. Treat as immutable once built."""

    graph: CodeGraph
    config: ProjectConfig
    report: AnalysisReport | None = None
    _fingerprint: tuple[float, ...] = field(default=(), repr=False)

    # -- adjacency ---------------------------------------------------------
    @cached_property
    def outgoing(self) -> dict[str, list[Relation]]:
        table: dict[str, list[Relation]] = defaultdict(list)
        for relation in self.graph.relations:
            table[relation.source].append(relation)
        return dict(table)

    @cached_property
    def incoming(self) -> dict[str, list[Relation]]:
        table: dict[str, list[Relation]] = defaultdict(list)
        for relation in self.graph.relations:
            table[relation.target].append(relation)
        return dict(table)

    def callers_of(self, symbol_id: str) -> list[Relation]:
        return self.incoming.get(symbol_id, [])

    def callees_of(self, symbol_id: str) -> list[Relation]:
        return self.outgoing.get(symbol_id, [])

    # -- symbol lookups ----------------------------------------------------
    @cached_property
    def by_name(self) -> dict[str, list[Symbol]]:
        table: dict[str, list[Symbol]] = defaultdict(list)
        for symbol in self.graph.symbols.values():
            table[symbol.name].append(symbol)
        return dict(table)

    @cached_property
    def members_of(self) -> dict[str, list[Symbol]]:
        """Direct child symbols of each class or container id."""
        table: dict[str, list[Symbol]] = defaultdict(list)
        for symbol in self.graph.symbols.values():
            if symbol.parent:
                table[symbol.parent].append(symbol)
        return dict(table)

    @cached_property
    def duplicate_names(self) -> set[tuple[str, str]]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for symbol in self.graph.symbols.values():
            counts[(symbol.kind, symbol.name)] += 1
        return {key for key, count in counts.items() if count > 1}

    # -- call depth --------------------------------------------------------
    @cached_property
    def call_adjacency(self) -> dict[str, list[str]]:
        """Symbol -> symbols it calls. Only real transfers of control, in-repo only."""
        table: dict[str, list[str]] = {sid: [] for sid in self.graph.symbols}
        for relation in self.graph.relations:
            if relation.kind not in CALL_KINDS:
                continue
            if relation.source in table and relation.target in self.graph.symbols:
                table[relation.source].append(relation.target)
        return table

    @cached_property
    def components(self) -> tuple[dict[str, int], list[list[str]]]:
        """Strongly connected components, by iterative Tarjan.

        Returned in reverse topological order, so a component's successors are
        always numbered lower than itself. Iterative rather than recursive because
        a deep call graph would otherwise need the interpreter recursion limit
        raised, which is a poor thing to do to a library's caller.
        """
        adjacency = self.call_adjacency
        index_of: dict[str, int] = {}
        low: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        component_of: dict[str, int] = {}
        components: list[list[str]] = []
        counter = 0

        for root in adjacency:
            if root in index_of:
                continue
            # (node, iterator position) frames, walked explicitly.
            work: list[tuple[str, int]] = [(root, 0)]
            index_of[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)

            while work:
                node, position = work[-1]
                successors = adjacency[node]
                if position < len(successors):
                    work[-1] = (node, position + 1)
                    successor = successors[position]
                    if successor not in index_of:
                        index_of[successor] = low[successor] = counter
                        counter += 1
                        stack.append(successor)
                        on_stack.add(successor)
                        work.append((successor, 0))
                    elif successor in on_stack:
                        low[node] = min(low[node], index_of[successor])
                    continue

                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index_of[node]:
                    members: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component_of[member] = len(components)
                        members.append(member)
                        if member == node:
                            break
                    components.append(members)

        return component_of, components

    @cached_property
    def max_call_chain(self) -> dict[str, int]:
        """Longest chain of calls starting at each symbol, in O(V+E).

        Computed as the longest path over the condensation of the call graph, so a
        mutually recursive group counts once. A cyclic component of size k
        contributes k-1 internally -- the longest path that visits each of its
        members at most once.

        Condensing rather than memoising a cycle-cutting DFS matters for more than
        elegance: the naive version's answer depended on which node the walk
        started from, so the same graph could report different numbers run to run.

        This replaces a per-symbol transitive walk that was O(V*E) and reported the
        size of the reachable set, which is not a depth at all.
        """
        component_of, components = self.components
        adjacency = self.call_adjacency

        component_depth = [0] * len(components)
        for number, members in enumerate(components):
            group = set(members)
            best = 0
            for member in members:
                for successor in adjacency[member]:
                    if successor not in group:
                        best = max(best, 1 + component_depth[component_of[successor]])
            cyclic = len(members) > 1 or any(m in adjacency[m] for m in members)
            internal = len(members) - 1 if cyclic else 0
            component_depth[number] = best + internal

        return {sid: component_depth[component_of[sid]] for sid in adjacency}


# --------------------------------------------------------------------------
# mtime-keyed cache
# --------------------------------------------------------------------------
_cache: dict[Path, GraphIndex] = {}


def _fingerprint(project: Path, config: ProjectConfig) -> tuple[float, ...]:
    """Mtimes of everything the index is derived from."""
    directory = project / config.output.directory
    stamps = []
    for name in (config.output.filename, "report.json"):
        path = directory / name
        stamps.append(path.stat().st_mtime if path.exists() else 0.0)
    return tuple(stamps)


def load_index(project: Path, config: ProjectConfig | None = None) -> GraphIndex:
    """Load (or reuse) the index for a project.

    Reuses the cached instance while the underlying files are unchanged, so a
    server handles many requests against one parse instead of one parse per
    request.
    """
    project = project.resolve()
    config = config or ProjectConfig.load(project)
    stamp = _fingerprint(project, config)

    cached = _cache.get(project)
    if cached is not None and cached._fingerprint == stamp:
        return cached

    index = GraphIndex(
        graph=load_graph(project),
        config=config,
        report=load_report(project),
        _fingerprint=stamp,
    )
    _cache[project] = index
    return index


def invalidate(project: Path | None = None) -> None:
    """Drop cached indexes. Call after writing a new graph in-process."""
    if project is None:
        _cache.clear()
    else:
        _cache.pop(project.resolve(), None)


def index_for(graph: CodeGraph, config: ProjectConfig | None = None) -> GraphIndex:
    """Wrap an in-memory graph, for callers that never touch disk (tests, CLI)."""
    return GraphIndex(graph=graph, config=config or ProjectConfig())


__all__ = [
    "CALL_KINDS",
    "GraphIndex",
    "index_for",
    "invalidate",
    "load_index",
]
