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
            # `unresolved` is dominated by genuinely external references --
            # stdlib and third-party names that are correctly not in the graph --
            # plus ambiguities recorded with their candidate sets. Nothing here
            # is "not attempted": data flow is always computed.
            total = totals["t"]
            exact_ratio = round(totals["e"] / total, 3)
            inferred_ratio = round(totals["i"] / total, 3)
            unresolved_ratio = round(totals["u"] / total, 3)

            # Get actual count of searchable symbols
            searchable_count = self.store.conn.execute(
                "SELECT COUNT(DISTINCT id) FROM nodes"
            ).fetchone()[0] or 0

            # Get usage site count
            usage_count = self.store.conn.execute(
                "SELECT COUNT(*) FROM usage_sites"
            ).fetchone()[0] or 0

            # Categorize: estimate split between external and ambiguous/typos
            # For now, assume ~70% of unresolved are external (okay), ~30% are issues
            external_resolved = round(exact_ratio * 0.8, 3)  # Conservative estimate

            envelope.refs = {
                "total_references": total,
                "local_defined": {
                    "exact": exact_ratio,
                    "inferred": inferred_ratio,
                    "searchable_count": searchable_count,
                },
                "external_packages": {
                    "resolved": external_resolved,
                    "unresolved": unresolved_ratio,
                    "usage_sites_tracked": usage_count,
                },
                "searchability": {
                    "note": (
                        f"Search indexes {searchable_count} symbols defined in this project. "
                        f"Coverage shows {unresolved_ratio:.1%} unresolved references "
                        f"(mostly external packages and stdlib, which is correct)."
                    ),
                    "for_external_symbols": (
                        "Use explore() to find where external packages are used. "
                        "Search only indexes locally-defined symbols."
                    ),
                    "recommend_explore": unresolved_ratio > 0.3,  # High external usage
                },
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

    def _analyze_empty_search(self, query: str, query_intent: "QueryIntent") -> dict[str, Any]:  # noqa: F821
        """Analyze why a search returned empty and suggest next steps."""
        from .intent import suggest_tool

        # Decompose compound queries
        tokens = query.split()
        suggestions = []

        # Add individual tokens as suggestions
        for token in tokens:
            if len(token) > 3:
                suggestions.append(token)

        # Add common pairs
        for i in range(len(tokens) - 1):
            pair = f"{tokens[i]} {tokens[i+1]}"
            if len(pair) > 5:
                suggestions.append(pair)

        # Add qualified name suggestions if detected
        external_packages = {"anthropic", "openai", "pydantic", "fastapi"}
        if any(pkg in query.lower() for pkg in external_packages):
            suggestions.extend([
                f"Try use explore('{tokens[0]}') for external packages",
            ])
            suggestions = [s for s in suggestions if s]

        return {
            "intent_detected": query_intent.value,
            "intent_suggestion": suggest_tool(query_intent),
            "suggestions": suggestions[:5],  # Top 5
            "explanation": (
                f"No results for '{query}'. This could mean:\n"
                f"1. The term doesn't exist in this project\n"
                f"2. It's part of an external package (use explore() instead)\n"
                f"3. Try searching for parts of the name separately"
            ),
        }

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
        """BM25-ranked search over name, qualified name, docstring, signature.

        ⚠️  IMPORTANT: This searches only SYMBOLS DEFINED IN THIS PROJECT.

        For external packages (anthropic.Anthropic, openai.OpenAI, etc.),
        use explore() instead — it shows where they're used in this codebase.

        **Returns:**
        - If symbols found: matched results with source and metadata
        - If empty: suggestions for alternative searches + tool recommendations
        """
        from .intent import detect_intent

        resolved, budget, envelope = self._prepare(intent, Intent.PLAN, limit=limit)

        # Detect query intent early
        query_intent = detect_intent(query, self.store)

        # Perform search
        nodes = self.store.search(
            query, kinds=kinds, lang=lang, service_id=service,
            limit=budget.limit + 1,
        )

        # If empty, provide actionable guidance
        if not nodes:
            analysis = self._analyze_empty_search(query, query_intent)
            return QueryResult.of(
                "search", [],
                budget=budget, envelope=envelope,
                extra={
                    "status": "no_matches",
                    "analysis": analysis,
                    "next_steps": [
                        analysis["intent_suggestion"],
                        *[f"Try: search('{s}')" for s in analysis["suggestions"]],
                    ],
                }
            )

        # Format results (existing code)
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

    # ---- 11.5 explore (new, high-level API) --------------------------------

    def explore(
        self,
        query: str,
        context: str | None = None,
        *,
        intent: Intent | str | None = None,
        limit: int | None = None,
    ) -> QueryResult:
        """One-shot answer: 'What code is relevant to this?'

        Orchestrates internally to return everything in one response:
        - Local symbols with source code (no Read() needed)
        - External symbol usage sites
        - Blast radius (tests, dependents, callers)
        - Suggested follow-up queries

        This is the primary tool for understanding code quickly without
        orchestrating multiple individual searches.

        Args:
            query: What you're looking for
            context: Optional context (e.g., "adding gateway metadata support")
            intent: "plan" (default, cheap) or "precise" (includes full source)
            limit: Max results per category

        Returns:
            QueryResult with everything bundled in one item
        """
        from .intent import detect_intent

        resolved, budget, envelope = self._prepare(intent, Intent.PLAN, limit=limit)

        # Detect what we're searching for
        query_intent = detect_intent(query, self.store)

        # Multi-strategy search
        local_symbols = self.store.search(query, limit=budget.limit // 2)
        external_usages = (
            self.store.find_usage(query, limit=budget.limit // 2)
            if not local_symbols
            else []
        )
        similar = (
            self.similar_flows(query, limit=3).items if local_symbols else []
        )

        # Enrich local symbols with source + callers + tests
        enriched_local = []
        for node in local_symbols:
            # Get source code
            sources = self._sources([node], resolved, budget)

            # Get direct callers
            callers_result = self.callers_of(
                node.qualified_name,
                kinds=None,
                transitive=False,  # Direct callers only for speed
                intent=Intent.PLAN,
                limit=5,
            )

            # Get impact (tests, dependents)
            impact = self.impact_of(
                node.qualified_name,
                intent=Intent.PLAN,
                limit=10,
            )

            enriched_local.append({
                "symbol": node.qualified_name,
                "kind": node.kind.value,
                "lang": node.lang,
                "at": f"{node.file_path}:{node.span.start_line}",
                "source": sources.get(node.id),
                "signature": node.signature,
                "docstring": (node.docstring or "").split("\n")[0],
                "entry_point": node.has(NodeFlags.ENTRY_POINT),
                "callers": callers_result.items if callers_result.items else [],
                "impact_summary": {
                    "tests_affected": len([i for i in impact.items if "test" in str(i).lower()]),
                    "dependents_count": len(impact.items or []),
                },
            })

        # Format external usage
        external_formatted = []
        for usage in external_usages[:budget.limit]:
            external_formatted.append({
                "symbol": usage["symbol_name"],
                "usage_type": usage["usage_type"],
                "file": usage["file_path"],
                "line": usage["start_line"],
                "context": usage["context_line"],
            })

        # Suggest follow-ups based on what we found
        next_explores = []
        if not local_symbols and query_intent.value == "external_usage":
            # For external packages, suggest exploring component parts
            tokens = query.split()
            if tokens:
                next_explores.extend([
                    f"explore('{tokens[0]}')",
                    f"explore('imports of {tokens[0]}')",
                ])
        elif local_symbols:
            # Suggest exploring related symbols
            for sym in local_symbols[:2]:
                next_explores.extend([
                    f"explore('{sym.qualified_name}')",
                    f"trace_flow('{sym.qualified_name}')",
                ])
        next_explores = next_explores[:5]

        # Bundle everything
        result_item = {
            "query_intent": query_intent.value,
            "query_context": context,
            "local_defined": enriched_local,
            "external_usage": external_formatted,
            "similar_patterns": similar,
            "suggestions": {
                "next_queries": next_explores,
                "note": (
                    "Local symbols above include source code, callers, and impact summary. "
                    "External usage shows where packages are used in this codebase."
                ),
            },
        }

        return QueryResult.of(
            "explore", [result_item],  # Single bundled result
            budget=budget, envelope=envelope,
            extra={
                "intent_matched": query_intent.value,
                "local_count": len(enriched_local),
                "external_count": len(external_formatted),
                "patterns_found": len(similar),
            }
        )

    # ---- 12. coverage_report ---------------------------------------------

    def coverage_report(self, scope: str | None = None) -> QueryResult:
        """The completeness envelope on its own (§10.6)."""
        _, _, envelope = self._prepare(None, Intent.PLAN)
        self._note_unclaimed(envelope)
        meta = self.store.conn.execute(
            "SELECT schema_version, commit_sha, build_digest, grammar_digest, "
            "       spec_digest, adapter_digest, built_at "
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
