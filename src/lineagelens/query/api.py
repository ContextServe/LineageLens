"""The twelve query primitives (issue #51 §10.2).

One entry point for the CLI, the MCP surface and the tests, so intent, budget
and envelope handling exist in exactly one place. Every primitive:

* takes ``intent`` (§10.1) and ``budget`` (§10.5), with per-primitive defaults
  chosen as the cheapest correct option,
* filters edge kinds **server-side** -- schema 3's docstrings told the agent to
  filter ``relation.kind`` itself, so full token cost was paid before most
  results were discarded,
* returns a :class:`~lineagelens.query.budget.QueryResult` carrying the
  completeness envelope (§10.6), so a short answer is never ambiguous between
  "that is all" and "we stopped".
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..core import CALL_CHAIN_EDGES, DATA_EDGES, EdgeKind, Intent, Node, NodeFlags
from ..store import GraphStore
from .analysis import Analyser
from .budget import Budget, Envelope, QueryResult
from .traverse import Traverser, kinds_for_intent, source_for


class QueryEngine:
    """Answers questions about one indexed project."""

    def __init__(self, store: GraphStore, project_root: str | None = None) -> None:
        self.store = store
        self.project_root = project_root or store.project_root
        self.traverser = Traverser(store)
        self.analyser = Analyser(store, self.project_root)

    # ---- setup ------------------------------------------------------------

    @classmethod
    def open(cls, project: Path | str) -> QueryEngine:
        from ..store import DB_FILENAME

        root = Path(project)
        store = GraphStore.open(root / ".lineagelens" / DB_FILENAME)
        return cls(store, str(root))

    def _prepare(
        self, intent: Intent | str | None, default: Intent, **overrides: Any
    ) -> tuple[Intent, Budget, Envelope]:
        resolved = Intent(intent) if intent is not None else default
        budget = Budget.for_intent(resolved, **overrides)
        envelope = Envelope(intent=resolved)
        self._seed_envelope(envelope)
        return resolved, budget, envelope

    def _seed_envelope(self, envelope: Envelope) -> None:
        """Populate repository-level coverage facts (§10.6)."""
        rows = self.store.conn.execute(
            "SELECT parse_status, count(*) n FROM files GROUP BY parse_status"
        ).fetchall()
        envelope.files = {r["parse_status"]: r["n"] for r in rows}

        totals = self.store.conn.execute(
            "SELECT sum(refs_total) t, sum(refs_exact) e, sum(refs_inferred) i, "
            "       sum(refs_unresolved) u FROM coverage"
        ).fetchone()
        if totals and totals["t"]:
            total = totals["t"]
            envelope.refs = {
                "total": total,
                "exact": round(totals["e"] / total, 3),
                "inferred": round(totals["i"] / total, 3),
                "unresolved": round(totals["u"] / total, 3),
            }

        for row in self.store.conn.execute(
            "SELECT lang, extractors FROM files GROUP BY lang"
        ):
            from ..core import loads

            runs = loads(row["extractors"]) or []
            tiers = sorted({str(r.get("tier", "?")) for r in runs})
            envelope.tier_used[row["lang"]] = "+".join(tiers) or "none"

        skipped = self.store.conn.execute(
            "SELECT DISTINCT lang FROM files WHERE skip_reason = 'missing_tier_b'"
        ).fetchall()
        envelope.degraded = [r["lang"] for r in skipped]

    def _node(self, ref: str) -> Node | None:
        """Resolve a node id, a qualified name, or a unique search hit."""
        node = self.store.get_node(ref)
        if node is not None:
            return node
        matches = self.store.nodes_by_qualified_name(ref)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            found = self.store.search(ref, limit=2)
            if len(found) == 1:
                return found[0]
        return None

    def _sources(self, nodes: Sequence[Node], intent: Intent, budget: Budget):
        if not budget.wants_source(intent):
            return {}
        return source_for(self.store, nodes, self.project_root)

    # ---- 1. find_paths ----------------------------------------------------

    def find_paths(
        self,
        start: str,
        goal: str,
        *,
        kinds: Sequence[str] | None = None,
        intent: Intent | str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        max_paths: int | None = None,
    ) -> QueryResult:
        """Ordered edge lists between two symbols -- the actual chains."""
        resolved, budget, envelope = self._prepare(
            intent, Intent.PLAN, limit=limit, max_depth=max_depth, max_paths=max_paths
        )
        src, dst = self._node(start), self._node(goal)
        if src is None or dst is None:
            return QueryResult(
                kind="find_paths", envelope=envelope,
                extra={"error": f"unknown symbol: {start if src is None else goal}"},
            )

        paths = self.traverser.find_paths(
            src.id, dst.id, kinds=kinds_for_intent(resolved, kinds),
            budget=budget, envelope=envelope,
        )
        sources = self._sources(
            [h.node for p in paths for h in p.hops], resolved, budget
        )
        return QueryResult.of(
            "find_paths", [p.as_dict(sources) for p in paths],
            budget=budget, envelope=envelope, ranking="shortest_path_first",
            from_symbol=src.qualified_name, to_symbol=dst.qualified_name,
        )

    # ---- 2. trace_flow ----------------------------------------------------

    def trace_flow(
        self,
        entry: str,
        *,
        kinds: Sequence[str] | None = None,
        intent: Intent | str | None = None,
        max_depth: int | None = None,
        limit: int | None = None,
    ) -> QueryResult:
        """A tree rooted at an entry point, not a bag of reachable symbols."""
        resolved, budget, envelope = self._prepare(
            intent, Intent.PLAN, max_depth=max_depth, limit=limit
        )
        node = self._node(entry)
        if node is None:
            return QueryResult(kind="trace_flow", envelope=envelope,
                               extra={"error": f"unknown symbol: {entry}"})
        tree = self.traverser.trace_flow(
            node.id, kinds=kinds_for_intent(resolved, kinds),
            budget=budget, envelope=envelope,
        )
        return QueryResult(
            kind="trace_flow", results=[tree], returned=1, total_available=1,
            envelope=envelope, extra={"root": node.qualified_name},
        )

    # ---- 3/4. callers_of / callees_of -------------------------------------

    def callers_of(
        self,
        symbol: str,
        *,
        kinds: Sequence[str] | None = None,
        transitive: bool = True,
        intent: Intent | str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
    ) -> QueryResult:
        """Caller chains with predecessors, not a flat reachability set."""
        return self._chains(
            symbol, forward=False, kinds=kinds, transitive=transitive,
            intent=intent, limit=limit, max_depth=max_depth, label="callers_of",
        )

    def callees_of(
        self,
        symbol: str,
        *,
        kinds: Sequence[str] | None = None,
        transitive: bool = True,
        intent: Intent | str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
    ) -> QueryResult:
        return self._chains(
            symbol, forward=True, kinds=kinds, transitive=transitive,
            intent=intent, limit=limit, max_depth=max_depth, label="callees_of",
        )

    def _chains(
        self, symbol: str, *, forward: bool, kinds, transitive: bool,
        intent, limit, max_depth, label: str,
    ) -> QueryResult:
        resolved, budget, envelope = self._prepare(
            intent, Intent.PLAN, limit=limit,
            max_depth=1 if not transitive else max_depth,
        )
        node = self._node(symbol)
        if node is None:
            return QueryResult(kind=label, envelope=envelope,
                               extra={"error": f"unknown symbol: {symbol}"})

        walk_kinds = [str(k) for k in (kinds or CALL_CHAIN_EDGES)]
        paths = self.traverser.walk(
            node.id, forward=forward, kinds=walk_kinds,
            budget=budget, envelope=envelope,
        )
        self.analyser._note_dispatch_boundaries(node, envelope)
        sources = self._sources([p.hops[-1].node for p in paths], resolved, budget)
        return QueryResult.of(
            label, [p.as_dict(sources) for p in paths],
            budget=budget, envelope=envelope, ranking="nearest_first",
            symbol=node.qualified_name,
        )

    # ---- 5. impact_of -----------------------------------------------------

    def impact_of(
        self,
        target: str,
        *,
        intent: Intent | str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
    ) -> QueryResult:
        """Typed blast radius for a symbol, a ``file:line``, or a diff.

        Defaults to ``precise`` for a ``file:line`` and ``plan`` for a symbol:
        a line is only ever asked about while debugging, whereas a symbol-level
        question is usually a survey.
        """
        line_target = _split_file_line(target)
        default = Intent.PRECISE if line_target else Intent.PLAN
        resolved, budget, envelope = self._prepare(
            intent, default, limit=limit, max_depth=max_depth
        )

        if line_target is not None:
            path, line = line_target
            report = self.analyser.impact_of_line(
                path, line, intent=resolved, budget=budget, envelope=envelope
            )
            if report is None:
                return QueryResult(
                    kind="impact_of", envelope=envelope,
                    extra={"error": f"no indexed symbol contains {path}:{line}"},
                )
        else:
            node = self._node(target)
            if node is None:
                return QueryResult(kind="impact_of", envelope=envelope,
                                   extra={"error": f"unknown symbol: {target}"})
            report = self.analyser.impact_of_node(
                node, intent=resolved, budget=budget, envelope=envelope
            )

        return QueryResult(
            kind="impact_of", results=[report], returned=1, total_available=1,
            envelope=envelope,
        )

    def impact_of_diff(
        self, diff: str, *, intent: Intent | str | None = None, limit: int | None = None
    ) -> QueryResult:
        """Aggregate impact over a unified diff, per changed hunk (§10.4).

        Makes the primitive usable directly in CI and PR review, where the
        question is always "what does *this change* affect" rather than "what
        does this symbol affect".
        """
        _, budget, envelope = self._prepare(intent, Intent.PRECISE, limit=limit)
        reports: list[Any] = []
        for path, line in _diff_touched_lines(diff):
            report = self.analyser.impact_of_line(
                path, line, intent=envelope.intent, budget=budget, envelope=envelope
            )
            if report is not None:
                reports.append(report)
        return QueryResult.of(
            "impact_of_diff", reports, budget=budget, envelope=envelope,
            ranking="diff_order",
        )

    # ---- 6. dataflow_of ---------------------------------------------------

    def dataflow_of(
        self,
        symbol: str,
        *,
        direction: str = "both",
        intent: Intent | str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
    ) -> QueryResult:
        """Forward/backward value flow with boundaries where it stops."""
        _, budget, envelope = self._prepare(
            intent, Intent.PRECISE, limit=limit, max_depth=max_depth
        )
        node = self._node(symbol)
        if node is None:
            return QueryResult(kind="dataflow_of", envelope=envelope,
                               extra={"error": f"unknown symbol: {symbol}"})
        flows = self.analyser.dataflow_of(
            node, direction=direction, budget=budget, envelope=envelope
        )
        return QueryResult.of(
            "dataflow_of", flows, budget=budget, envelope=envelope,
            ranking="nearest_first", symbol=node.qualified_name,
            kinds_followed=sorted(
                str(k) for k in DATA_EDGES if k is not EdgeKind.FLOWS_TO
            ),
        )

    # ---- 7. contract_map --------------------------------------------------

    def contract_map(
        self,
        *,
        service: str | None = None,
        kind: str | None = None,
        intent: Intent | str | None = None,
        limit: int | None = None,
    ) -> QueryResult:
        """Who exposes and consumes what, across services (§8)."""
        _, budget, envelope = self._prepare(intent, Intent.PLAN, limit=limit)
        entries = self.analyser.contract_map(service=service, kind=kind)
        self._note_unclaimed(envelope)
        return QueryResult.of(
            "contract_map", entries, budget=budget, envelope=envelope,
            ranking="contract_key",
            cross_service=sum(1 for e in entries if e["cross_service"]),
            dangling=sum(1 for e in entries if e["dangling"]),
        )

    def _note_unclaimed(self, envelope: Envelope) -> None:
        """Report frameworks present but unmodelled (§8.2).

        Without this an empty contract map is indistinguishable from "these
        services genuinely are not connected".
        """
        row = self.store.conn.execute(
            "SELECT ontology_digest FROM graph_meta WHERE id = 1"
        ).fetchone()
        if row and row["ontology_digest"]:
            from ..core import loads

            payload = loads(row["ontology_digest"])
            if isinstance(payload, dict):
                envelope.unclaimed_frameworks = payload.get("unclaimed", [])

    # ---- 8. similar_flows -------------------------------------------------

    def similar_flows(
        self, exemplar: str, *, limit: int | None = None,
        intent: Intent | str | None = None,
    ) -> QueryResult:
        """The canonical wiring shape of an exemplar's peers (§10.3)."""
        _, budget, envelope = self._prepare(intent, Intent.PLAN, limit=limit)
        node = self._node(exemplar)
        if node is None:
            return QueryResult(kind="similar_flows", envelope=envelope,
                               extra={"error": f"unknown symbol: {exemplar}"})
        payload = self.analyser.similar_flows(
            node, budget=budget, envelope=envelope
        )
        return QueryResult(
            kind="similar_flows", results=[payload], returned=1,
            total_available=1, envelope=envelope,
        )

    # ---- 9. explain -------------------------------------------------------

    def explain(self, src: str, dst: str) -> QueryResult:
        """Evidence, provenance and spans for the edges between two nodes."""
        _, budget, envelope = self._prepare(None, Intent.PRECISE)
        a, b = self._node(src), self._node(dst)
        if a is None or b is None:
            return QueryResult(kind="explain", envelope=envelope,
                               extra={"error": "unknown symbol"})
        return QueryResult.of(
            "explain", self.analyser.explain(a.id, b.id),
            budget=budget, envelope=envelope,
            from_symbol=a.qualified_name, to_symbol=b.qualified_name,
        )

    # ---- 10. map_stacktrace ----------------------------------------------

    def map_stacktrace(
        self, text: str, *, intent: Intent | str | None = None,
        limit: int | None = None,
    ) -> QueryResult:
        """Stack frames -> nodes -> the operations on each frame's line."""
        _, budget, envelope = self._prepare(intent, Intent.PRECISE, limit=limit)
        frames = self.analyser.map_stacktrace(
            text, budget=budget, envelope=envelope
        )
        return QueryResult.of(
            "map_stacktrace", frames, budget=budget, envelope=envelope,
            ranking="innermost_frame_first",
        )

    # ---- 11. search -------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        kinds: Sequence[str] | None = None,
        lang: str | None = None,
        service: str | None = None,
        intent: Intent | str | None = None,
        limit: int | None = None,
    ) -> QueryResult:
        """BM25-ranked search over name, qualified name, docstring, signature."""
        resolved, budget, envelope = self._prepare(intent, Intent.PLAN, limit=limit)
        nodes = self.store.search(
            query, kinds=kinds, lang=lang, service_id=service,
            limit=budget.limit + 1,
        )
        sources = self._sources(nodes[: budget.limit], resolved, budget)
        items = [
            {
                "node": n.qualified_name,
                "kind": n.kind.value,
                "lang": n.lang,
                "at": f"{n.file_path}:{n.span.start_line}",
                "signature": n.signature,
                "docstring": (n.docstring or "").split("\n")[0] or None,
                "entry_point": n.has(NodeFlags.ENTRY_POINT),
                **({"source": sources[n.id]} if n.id in sources else {}),
            }
            for n in nodes
        ]
        return QueryResult.of(
            "search", items, budget=budget, envelope=envelope, ranking="bm25",
        )

    # ---- 12. coverage_report ---------------------------------------------

    def coverage_report(self, scope: str | None = None) -> QueryResult:
        """The completeness envelope on its own (§10.6)."""
        _, _, envelope = self._prepare(None, Intent.PLAN)
        self._note_unclaimed(envelope)
        meta = self.store.conn.execute(
            "SELECT schema_version, commit_sha, build_digest, grammar_digest, "
            "       spec_digest, adapter_digest, dataflow_mode, built_at "
            "FROM graph_meta WHERE id = 1"
        ).fetchone()

        detail: list[dict[str, Any]] = []
        if scope:
            for row in self.store.conn.execute(
                "SELECT f.path, f.lang, f.parse_status, f.skip_reason, "
                "       c.refs_total, c.refs_exact, c.refs_inferred, "
                "       c.refs_unresolved, c.dataflow_status "
                "FROM files f LEFT JOIN coverage c ON c.file_id = f.id "
                "WHERE f.path LIKE ? ORDER BY f.path LIMIT 200",
                (f"{scope}%",),
            ):
                detail.append(dict(row))

        return QueryResult(
            kind="coverage_report", results=detail, returned=len(detail),
            total_available=len(detail), envelope=envelope,
            extra={
                "counts": self.store.counts(),
                "node_kinds": self.store.node_kind_counts(),
                "edge_kinds": self.store.edge_kind_counts(),
                "build": dict(meta) if meta else {},
            },
        )

    # ---- convenience ------------------------------------------------------

    def get_node(
        self, symbol: str, *, intent: Intent | str | None = None
    ) -> QueryResult:
        """One node with its signature, docstring, and (if precise) source."""
        resolved, budget, envelope = self._prepare(intent, Intent.PRECISE)
        node = self._node(symbol)
        if node is None:
            return QueryResult(kind="get_node", envelope=envelope,
                               extra={"error": f"unknown symbol: {symbol}"})
        sources = self._sources([node], resolved, budget)
        payload = {
            "node": node.qualified_name,
            "kind": node.kind.value,
            "lang": node.lang,
            "service": node.service_id,
            "at": f"{node.file_path}:{node.span.start_line}-{node.span.end_line}",
            "signature": node.signature,
            "return_type": node.return_type,
            "type": node.type_ref,
            "visibility": node.visibility.value if node.visibility else None,
            "docstring": node.docstring,
            "decorators": list(node.decorators),
            "flags": [f.name for f in NodeFlags if f.value and node.has(f)],
            "source": sources.get(node.id),
        }
        return QueryResult(
            kind="get_node", results=[{k: v for k, v in payload.items() if v}],
            returned=1, total_available=1, envelope=envelope,
        )

    def entry_points(
        self, *, kind: str | None = None, limit: int | None = None
    ) -> QueryResult:
        """Symbols exposing a contract -- the roots every trace starts from."""
        _, budget, envelope = self._prepare(None, Intent.PLAN, limit=limit)
        rows = self.store.conn.execute(
            "SELECT DISTINCT e.src, e.metadata FROM edges e WHERE e.kind = 'EXPOSES'"
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            node = self.store.get_node(row["src"])
            if node is None:
                continue
            from ..core import loads

            meta = loads(row["metadata"]) or {}
            if kind and meta.get("contract_kind") != kind:
                continue
            items.append({
                "node": node.qualified_name,
                "contract_kind": meta.get("contract_kind", ""),
                "contract": meta.get("contract_key", ""),
                "at": f"{node.file_path}:{node.span.start_line}",
            })
        items.sort(key=lambda e: (e["contract_kind"], e["contract"], e["node"]))
        return QueryResult.of(
            "entry_points", items, budget=budget, envelope=envelope,
            ranking="contract_kind",
        )


# ---------------------------------------------------------------------------
# target parsing
# ---------------------------------------------------------------------------

def _split_file_line(target: str) -> tuple[str, int] | None:
    """Parse ``path/to/file.py:412``, or ``None`` if it is not that shape.

    Rejects a bare number and a Windows drive letter, and requires the tail to
    be an integer -- so a qualified name containing a colon is not mistaken for
    a file reference.
    """
    if ":" not in target:
        return None
    path, _, tail = target.rpartition(":")
    if not path or not tail.isdigit():
        return None
    return (path, int(tail))


def _diff_touched_lines(diff: str) -> list[tuple[str, int]]:
    """``(path, line)`` for every added or context line in a unified diff.

    New-file line numbers are tracked, because those are the lines that exist
    after the change and therefore the ones the index can resolve.
    """
    import re

    touched: list[tuple[str, int]] = []
    header = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")

    path: str | None = None
    line = 0
    for row in diff.splitlines():
        match = header.match(row)
        if match:
            path = match.group("path")
            continue
        match = hunk.match(row)
        if match:
            line = int(match.group("start"))
            continue
        if path is None:
            continue
        if row.startswith("+") and not row.startswith("+++"):
            touched.append((path, line))
            line += 1
        elif row.startswith("-"):
            continue  # removed lines do not exist in the indexed revision
        elif row.startswith(" "):
            line += 1
    return touched
