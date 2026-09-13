"""Path and trace primitives (issue #51 §10.2).

These replace ``get_lineage``, which returned a *reachability set*: a flat list
of ``(symbol_id, relation_kind, depth, resolution)`` with no predecessor link.
Three consequences, all measured:

* No chain was reconstructible. "Who reaches ``analyze``?" returned a bag of
  155 unordered symbols, which cannot answer "what is the call chain from the
  CLI to ``analyze``" -- the single most common tracing question.
* Depths were wrong. Named ``bfs`` but recursing immediately, it was a DFS with
  a global ``visited`` set, so on ``A->B->C->D`` plus ``A->D`` it reported D at
  depth 3 when the shortest path is 1.
* ``direction="both"`` shared one ``visited`` set across both walks and returned
  a single flat list with no direction field, so a caller could not be
  distinguished from a callee.

Returning paths rather than sets makes all three unrepresentable: a path *is*
its predecessor chain, its length *is* the true distance, and forward and
backward walks return separately typed results.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..core import CALL_CHAIN_EDGES, Edge, EdgeKind, Intent, Node
from ..store import GraphStore
from .budget import Budget, Envelope

#: Edge kinds a path walk follows when the caller does not say. Excludes
#: CONTAINS, which would let a walk escape through a shared parent and reach
#: every sibling in a module.
DEFAULT_PATH_KINDS = CALL_CHAIN_EDGES


@dataclass(frozen=True, slots=True)
class Hop:
    """One edge on a path, with the node it arrives at."""

    node: Node
    edge: Edge | None
    depth: int

    def as_dict(self, *, source: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "depth": self.depth,
            "node": self.node.qualified_name,
            "kind": self.node.kind.value,
            "at": f"{self.node.file_path}:{self.node.span.start_line}",
        }
        if self.edge is not None:
            out["via"] = self.edge.kind.value
            out["evidence"] = self.edge.evidence.tier.value
            if self.edge.span is not None:
                out["written_at"] = f"{self.edge.file_path}:{self.edge.span.start_line}"
        if self.node.signature:
            out["signature"] = self.node.signature
        if source is not None:
            out["source"] = source
        return out


@dataclass(slots=True)
class Path:
    """An ordered chain of hops. The thing ``get_lineage`` could not express."""

    hops: list[Hop] = field(default_factory=list)
    direction: str = "forward"

    @property
    def length(self) -> int:
        """Number of edges, which is the true distance -- not a DFS artefact."""
        return max(0, len(self.hops) - 1)

    @property
    def endpoints(self) -> tuple[str, str]:
        return (self.hops[0].node.id, self.hops[-1].node.id) if self.hops else ("", "")

    def signature(self) -> tuple[str, ...]:
        """Motif signature: the kinds traversed. Used by ``similar_flows``."""
        return tuple(h.edge.kind.value for h in self.hops if h.edge is not None)

    def as_dict(self, sources: dict[str, str] | None = None) -> dict[str, Any]:
        sources = sources or {}
        return {
            "direction": self.direction,
            "length": self.length,
            "chain": " -> ".join(h.node.name for h in self.hops),
            "hops": [h.as_dict(source=sources.get(h.node.id)) for h in self.hops],
        }


class Traverser:
    """Breadth-first walks that return paths."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ---- neighbours -------------------------------------------------------

    def _step(
        self, node_id: str, kinds: Sequence[str], forward: bool
    ) -> list[tuple[str, Edge]]:
        edges = (
            self.store.edges_from(node_id, kinds)
            if forward
            else self.store.edges_to(node_id, kinds)
        )
        return [((e.dst if forward else e.src), e) for e in edges]

    # ---- primitives -------------------------------------------------------

    def find_paths(
        self,
        start: str,
        goal: str,
        *,
        kinds: Sequence[str] | None = None,
        budget: Budget,
        envelope: Envelope,
    ) -> list[Path]:
        """Every distinct path from ``start`` to ``goal``, shortest first.

        Genuinely breadth-first, with the visited set scoped *per path* rather
        than globally. A global set is what made ``get_lineage`` report a
        1-hop target at depth 3: once a node was seen on any branch it could
        never appear on a shorter one.
        """
        kinds = list(kinds or DEFAULT_PATH_KINDS)
        kind_values = [str(k) for k in kinds]

        found: list[Path] = []
        seen_signatures: set[tuple[str, ...]] = set()
        queue: deque[list[tuple[str, Edge | None]]] = deque([[(start, None)]])

        while queue and len(found) < budget.max_paths:
            chain = queue.popleft()
            current = chain[-1][0]

            if current == goal and len(chain) > 1:
                key = tuple(node for node, _ in chain)
                if key not in seen_signatures:
                    seen_signatures.add(key)
                    built = self._materialise(chain)
                    if built is not None:
                        found.append(built)
                continue

            if len(chain) > budget.max_depth:
                envelope.add_boundary(
                    "depth_limit",
                    detail=f"path search stopped at depth {budget.max_depth}",
                )
                continue

            on_path = {node for node, _ in chain}
            for nxt, edge in self._step(current, kind_values, forward=True):
                if nxt in on_path:
                    continue  # a cycle, not a path
                queue.append([*chain, (nxt, edge)])

        found.sort(key=lambda p: (p.length, p.signature()))
        return found

    def walk(
        self,
        start: str,
        *,
        forward: bool,
        kinds: Sequence[str] | None = None,
        budget: Budget,
        envelope: Envelope,
    ) -> list[Path]:
        """Paths from ``start`` outward, one per reachable node.

        Each result carries its own chain, so "who calls this, and how" is
        answerable rather than just "what is reachable".
        """
        kinds = list(kinds or DEFAULT_PATH_KINDS)
        kind_values = [str(k) for k in kinds]
        direction = "forward" if forward else "backward"

        paths: list[Path] = []
        # Shortest-path-per-node: breadth-first with a global visited set is
        # correct *here* because each node is reported once, at its true
        # minimum distance. That differs from find_paths, which needs every
        # route and therefore scopes visited per path.
        visited = {start}
        queue: deque[list[tuple[str, Edge | None]]] = deque([[(start, None)]])

        while queue:
            chain = queue.popleft()
            if len(chain) - 1 >= budget.max_depth:
                if self._step(chain[-1][0], kind_values, forward):
                    envelope.add_boundary(
                        "depth_limit",
                        detail=f"walk stopped at depth {budget.max_depth}",
                    )
                continue

            for nxt, edge in self._step(chain[-1][0], kind_values, forward):
                if nxt in visited:
                    continue
                visited.add(nxt)
                extended = [*chain, (nxt, edge)]
                built = self._materialise(extended, direction=direction)
                if built is not None:
                    paths.append(built)
                queue.append(extended)

        paths.sort(key=lambda p: (p.length, p.hops[-1].node.qualified_name))
        return paths

    def trace_flow(
        self,
        entry: str,
        *,
        kinds: Sequence[str] | None = None,
        budget: Budget,
        envelope: Envelope,
    ) -> dict[str, Any]:
        """A tree rooted at ``entry``, not a bag of reachable symbols."""
        kinds = list(kinds or DEFAULT_PATH_KINDS)
        kind_values = [str(k) for k in kinds]

        root = self.store.get_node(entry)
        if root is None:
            return {}

        visited = {entry}
        nodes_emitted = 0

        def build(node: Node, depth: int) -> dict[str, Any]:
            nonlocal nodes_emitted
            payload: dict[str, Any] = {
                "node": node.qualified_name,
                "kind": node.kind.value,
                "at": f"{node.file_path}:{node.span.start_line}",
            }
            if depth >= budget.max_depth or nodes_emitted >= budget.limit:
                if self._step(node.id, kind_values, forward=True):
                    payload["truncated"] = True
                    envelope.add_boundary(
                        "depth_limit" if depth >= budget.max_depth else "budget",
                        detail=f"{node.qualified_name} not expanded",
                    )
                return payload

            children: list[dict[str, Any]] = []
            for nxt, edge in self._step(node.id, kind_values, forward=True):
                if nxt in visited:
                    continue
                visited.add(nxt)
                child_node = self.store.get_node(nxt)
                if child_node is None:
                    continue
                nodes_emitted += 1
                child = build(child_node, depth + 1)
                child["via"] = edge.kind.value
                children.append(child)
            if children:
                payload["calls"] = children
            return payload

        return build(root, 0)

    # ---- helpers ----------------------------------------------------------

    def _materialise(
        self, chain: Sequence[tuple[str, Edge | None]], direction: str = "forward"
    ) -> Path | None:
        """Turn id/edge pairs into a Path, dropping it if a node has vanished."""
        hops: list[Hop] = []
        for depth, (node_id, edge) in enumerate(chain):
            node = self.store.get_node(node_id)
            if node is None:
                return None
            hops.append(Hop(node=node, edge=edge, depth=depth))
        return Path(hops=hops, direction=direction)


def source_for(
    store: GraphStore, nodes: Iterable[Node], project_root: str, *, max_lines: int = 60
) -> dict[str, str]:
    """Verbatim, line-numbered source for each node (§13.2).

    The single largest unrealised value in the schema-3 product: no tool read
    file text, so an agent received ids and line numbers and then had to
    ``Read`` the files anyway -- paying the exact cost the graph exists to
    avoid.

    Long bodies are head-and-tail elided rather than truncated, because a
    function's signature and its return are usually the two parts that matter.
    """
    from pathlib import Path as FsPath

    out: dict[str, str] = {}
    cache: dict[str, list[str]] = {}
    for node in nodes:
        if not node.file_path:
            continue
        lines = cache.get(node.file_path)
        if lines is None:
            try:
                lines = (
                    FsPath(project_root, node.file_path)
                    .read_text("utf-8", errors="replace")
                    .splitlines()
                )
            except OSError:
                lines = []
            cache[node.file_path] = lines
        if not lines:
            continue

        start = max(1, node.span.start_line)
        end = min(len(lines), node.span.end_line or start)
        if end - start + 1 > max_lines:
            head = [
                f"{n:>5}  {lines[n - 1]}"
                for n in range(start, start + max_lines // 2)
            ]
            tail = [
                f"{n:>5}  {lines[n - 1]}"
                for n in range(end - max_lines // 2 + 1, end + 1)
            ]
            elided = end - start + 1 - max_lines
            out[node.id] = "\n".join(
                [*head, f"       ... {elided} lines elided ...", *tail]
            )
        else:
            out[node.id] = "\n".join(
                f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1)
            )
    return out


def kinds_for_intent(intent: Intent, requested: Sequence[str] | None) -> list[str]:
    """Edge kinds to follow, given an intent.

    ``plan`` follows contract edges as well as calls, because a survey wants to
    see the service boundary; ``precise`` adds the data edges a debugging answer
    needs. An explicit request always wins.
    """
    if requested:
        return [str(k) for k in requested]
    base = set(DEFAULT_PATH_KINDS)
    if intent is Intent.PLAN:
        base |= {EdgeKind.EXPOSES, EdgeKind.CONSUMES}
    else:
        base |= {
            EdgeKind.EXPOSES, EdgeKind.CONSUMES,
            EdgeKind.PARAM_BINDS, EdgeKind.RETURNS,
        }
    return sorted(str(k) for k in base)
