"""REST API for LineageLens (shared by web frontend and ChatGPT Actions).

Provides both read-only queries and a trigger for analysis.
Uses queries.py as the single source of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from .analyzer import analyze
from .config import ProjectConfig
from .index import invalidate, load_index
from .queries import (
    GraphNotFoundError,
    get_callees,
    get_callers,
    get_lineage,
    get_module_overview,
    get_symbol,
    impact_analysis,
    is_test_path,
    list_entry_points,
    list_resiliency_risks,
    search_symbols,
)
from .reachability import compute_reachability


# Pydantic models for responses
class SymbolOut(BaseModel):
    id: str
    kind: str
    name: str
    file: str
    line: int
    end_line: int | None
    module: str
    entry_point: str | None
    async_: bool
    description: str | None
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    decorators: list[str]


class RelationOut(BaseModel):
    source: str
    target: str
    kind: str
    file: str
    line: int
    resolution: str


class NodeView(BaseModel):
    """Lightweight node for graph visualization (not full detail)."""

    id: str
    label: str
    kind: str
    parent: str | None
    entry_point: str | None
    async_: bool
    has_resiliency_flag: bool
    is_test: bool = False
    # Reachability verdict: alive | dynamic_only | test_only | public_api |
    # probably_dead | dead. Containers have none.
    verdict: str | None = None
    # The specific mechanism that kept it alive, and that mechanism's trust tier.
    # A verdict an agent cannot audit is one it should not act on.
    rescue_mechanism: str | None = None
    rescue_tier: str | None = None
    scope: str = "source"
    duplicate_name: bool = False


class EdgeView(BaseModel):
    """Edge for graph visualization."""

    id: str
    source: str
    target: str
    kind: str
    resolution: str


class GraphView(BaseModel):
    """Full graph for visualization (nodes + edges)."""

    nodes: list[NodeView]
    edges: list[EdgeView]


class LineageStepOut(BaseModel):
    symbol_id: str
    relation_kind: str
    depth: int
    resolution: str


class ImpactReportOut(BaseModel):
    symbol_id: str
    affected: list[LineageStepOut]
    affected_entry_points: list[str]


class ModuleOverviewOut(BaseModel):
    module_id: str
    symbols: list[dict[str, Any]]
    submodules: list[str]


class RiskOut(BaseModel):
    symbol_id: str
    category: str
    severity: str
    evidence: str
    line: int


def create_router(project: Path, api_key: str | None = None) -> APIRouter:
    """Create a REST router for a project.

    Args:
        project: Project root path
        api_key: Optional API key for protected endpoints (POST /analyze)

    Returns:
        FastAPI Router with all endpoints
    """
    router = APIRouter(prefix="/api/v1", tags=["lineagelens"])

    # Optional auth dependency
    def verify_api_key(x_api_key: str | None = Header(None)) -> None:
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # GET /api/v1/graph/view - for frontend visualization
    @router.get("/graph/view", response_model=GraphView)
    def graph_view(module: str | None = None) -> GraphView:
        """Get graph data for visualization (with optional module filtering).

        For large repos, module scoping allows lazy loading one module at a time.
        Includes both Symbols and Containers for compound (hierarchical) layout.
        """
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        config = index.config

        # Precompute unreferenced symbols and duplicate names for efficiency
        reachability = compute_reachability(graph, config)
        duplicate_names = index.duplicate_names

        # Collect nodes
        nodes = []
        rendered_node_ids = set()  # Track which nodes we're actually rendering

        # First pass: emit Container nodes (packages and modules)
        for container in graph.containers.values():
            # Skip if filtering by module and this container isn't in/under that module
            # In scope if the id matches the module or sits under it.
            if module and container.id != module and not container.id.startswith(module + "."):
                continue

            has_risk = False  # Containers don't have risk flags directly
            is_test = is_test_path(container.file, test_roots=config.test_roots)

            # Defensive: null out parent if it won't exist in rendered nodes
            # (This happens after filtering in both module-scoped and test-filtered cases)
            parent_id = container.parent
            # We'll validate parent refs after all nodes are collected

            node = NodeView(
                id=container.id,
                label=container.name,
                kind=container.kind,
                parent=parent_id,
                entry_point=None,
                async_=False,
                has_resiliency_flag=has_risk,
                is_test=is_test,
                verdict=None,
                scope="test" if is_test else "source",
                duplicate_name=False,
            )
            nodes.append(node)
            rendered_node_ids.add(container.id)

        # Second pass: emit Symbol nodes
        for symbol in graph.symbols.values():
            # Skip if filtering by module and this symbol's parent doesn't match
            if module and symbol.parent != module and not (
                symbol.parent and symbol.parent.startswith(module + ".")
            ):
                continue

            has_risk = len(symbol.resiliency) > 0
            is_test = is_test_path(symbol.file, test_roots=config.test_roots)
            candidate = reachability.explain(symbol.id)
            duplicate_name = (symbol.kind, symbol.name) in duplicate_names

            # Defensive: null out parent if it won't exist in rendered nodes
            parent_id = symbol.parent

            node = NodeView(
                id=symbol.id,
                label=symbol.name,
                kind=symbol.kind,
                parent=parent_id,
                entry_point=symbol.entry_point,
                async_=symbol.async_,
                has_resiliency_flag=has_risk,
                is_test=is_test,
                verdict=candidate.verdict if candidate else None,
                rescue_mechanism=(
                    candidate.rescue.name if candidate and candidate.rescue else None
                ),
                rescue_tier=(
                    candidate.rescue.evidence.tier if candidate and candidate.rescue else None
                ),
                scope=candidate.scope if candidate else "source",
                duplicate_name=duplicate_name,
            )
            nodes.append(node)
            rendered_node_ids.add(symbol.id)

        # Third pass: defensive cleanup — null out any parent refs that don't resolve
        for node in nodes:
            if node.parent and node.parent not in rendered_node_ids:
                node.parent = None

        # Collect edges
        # Only include edges where BOTH source and target exist in the rendered nodes
        # (Cytoscape requires both endpoints to exist)
        edges = []
        for i, rel in enumerate(graph.relations):
            # Skip if either endpoint is not in the rendered nodes
            if rel.source not in rendered_node_ids or rel.target not in rendered_node_ids:
                continue

            edge = EdgeView(
                id=f"rel_{i}",
                source=rel.source,
                target=rel.target,
                kind=rel.kind,
                resolution=rel.resolution,
            )
            edges.append(edge)

        return GraphView(nodes=nodes, edges=edges)

    # GET /api/v1/symbols/{symbol_id} - full symbol detail
    @router.get("/symbols/{symbol_id}", response_model=SymbolOut)
    def get_symbol_detail(symbol_id: str) -> SymbolOut:
        """Get full details for a symbol."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        symbol = get_symbol(graph, symbol_id)
        if not symbol:
            raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol_id}")

        return SymbolOut(
            id=symbol.id,
            kind=symbol.kind,
            name=symbol.name,
            file=symbol.file,
            line=symbol.line,
            end_line=symbol.end_line,
            module=symbol.module,
            entry_point=symbol.entry_point,
            async_=symbol.async_,
            description=symbol.description,
            inputs=symbol.inputs,
            outputs=symbol.outputs,
            decorators=symbol.decorators,
        )

    # GET /api/v1/search - search symbols
    @router.get("/search", response_model=list[SymbolOut])
    def search(text: str, kind: str | None = None, limit: int = 30) -> list[SymbolOut]:
        """Search symbols by name/id."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        results = search_symbols(graph, text, kind=kind, limit=limit)
        return [
            SymbolOut(
                id=s.id,
                kind=s.kind,
                name=s.name,
                file=s.file,
                line=s.line,
                end_line=s.end_line,
                module=s.module,
                entry_point=s.entry_point,
                async_=s.async_,
                description=s.description,
                inputs=s.inputs,
                outputs=s.outputs,
                decorators=s.decorators,
            )
            for s in results
        ]

    # GET /api/v1/symbols/{symbol_id}/callers - who calls this?
    @router.get("/symbols/{symbol_id}/callers", response_model=list[RelationOut])
    def callers(symbol_id: str) -> list[RelationOut]:
        """Get all symbols that call this one."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        relations = get_callers(graph, symbol_id, index)
        return [
            RelationOut(
                source=r.source,
                target=r.target,
                kind=r.kind,
                file=r.file,
                line=r.line,
                resolution=r.resolution,
            )
            for r in relations
        ]

    # GET /api/v1/symbols/{symbol_id}/callees - what does this call?
    @router.get("/symbols/{symbol_id}/callees", response_model=list[RelationOut])
    def callees(symbol_id: str) -> list[RelationOut]:
        """Get all symbols this one calls."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        relations = get_callees(graph, symbol_id, index)
        return [
            RelationOut(
                source=r.source,
                target=r.target,
                kind=r.kind,
                file=r.file,
                line=r.line,
                resolution=r.resolution,
            )
            for r in relations
        ]

    # GET /api/v1/symbols/{symbol_id}/lineage - transitive call path
    @router.get("/symbols/{symbol_id}/lineage", response_model=list[LineageStepOut])
    def lineage(symbol_id: str, direction: str = "forward", max_depth: int = 5) -> list[LineageStepOut]:
        """Get transitive call path (forward or backward)."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        steps = get_lineage(graph, symbol_id, direction=direction, max_depth=max_depth)
        return [
            LineageStepOut(
                symbol_id=s.symbol_id,
                relation_kind=s.relation_kind,
                depth=s.depth,
                resolution=s.resolution,
            )
            for s in steps
        ]

    # GET /api/v1/symbols/{symbol_id}/impact - what breaks if I change this?
    @router.get("/symbols/{symbol_id}/impact", response_model=ImpactReportOut)
    def impact(symbol_id: str, max_depth: int = 10) -> ImpactReportOut:
        """Backward transitive closure: what would be affected by changes here?"""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        report = impact_analysis(graph, symbol_id, max_depth=max_depth)
        return ImpactReportOut(
            symbol_id=report.symbol_id,
            affected=[
                LineageStepOut(
                    symbol_id=s.symbol_id,
                    relation_kind=s.relation_kind,
                    depth=s.depth,
                    resolution=s.resolution,
                )
                for s in report.affected
            ],
            affected_entry_points=report.affected_entry_points,
        )

    # GET /api/v1/modules/{module}/overview - module summary
    @router.get("/modules/{module}/overview", response_model=ModuleOverviewOut)
    def module_overview(module: str) -> ModuleOverviewOut:
        """Get high-level overview of a module (for agents)."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        overview = get_module_overview(graph, module)
        if not overview:
            raise HTTPException(status_code=404, detail=f"Module not found: {module}")

        return ModuleOverviewOut(
            module_id=overview.module_id,
            symbols=overview.symbols,
            submodules=overview.submodules,
        )

    # GET /api/v1/entry-points - list all entry points
    @router.get("/entry-points", response_model=list[SymbolOut])
    def entry_points(kind: str | None = None) -> list[SymbolOut]:
        """List all entry points (API routes, CLI commands, tests)."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        entries = list_entry_points(graph, kind=kind)
        return [
            SymbolOut(
                id=s.id,
                kind=s.kind,
                name=s.name,
                file=s.file,
                line=s.line,
                end_line=s.end_line,
                module=s.module,
                entry_point=s.entry_point,
                async_=s.async_,
                description=s.description,
                inputs=s.inputs,
                outputs=s.outputs,
                decorators=s.decorators,
            )
            for s in entries
        ]

    # GET /api/v1/resiliency - list risk signals
    @router.get("/resiliency", response_model=list[RiskOut])
    def resiliency(min_severity: str | None = None) -> list[RiskOut]:
        """List all resiliency/risk signals."""
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        risks = list_resiliency_risks(graph, min_severity=min_severity)
        return [RiskOut(**r) for r in risks]

    # GET /api/v1/dead-code - the full candidate list with verdicts
    @router.get("/dead-code")
    def dead_code(
        verdict: str | None = None, scope: str | None = None
    ) -> dict[str, Any]:
        """Symbols that warrant attention as possible dead code.

        Previously reachable only as node flags on /graph/view or through MCP.

        Verdicts, in descending severity:
          dead           not reachable from any entry point by any modelled
                         mechanism, and no same-named dynamic call site exists
          probably_dead  unreachable, but an unresolved call shares its name
          test_only      reachable only from tests, so nothing shipped uses it
        """
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        result = compute_reachability(graph, index.config)
        candidates = result.candidates()
        if verdict:
            candidates = [c for c in candidates if c.verdict == verdict]
        if scope:
            candidates = [c for c in candidates if c.scope == scope]

        return {
            "count": len(candidates),
            "by_verdict": result.by_verdict(),
            "candidates": [
                {
                    "id": c.symbol.id,
                    "kind": c.symbol.kind,
                    "name": c.symbol.name,
                    "file": c.symbol.file,
                    "line": c.symbol.line,
                    "module": c.symbol.module,
                    "verdict": c.verdict,
                    "scope": c.scope,
                    "reason": c.reason,
                }
                for c in candidates
            ],
        }

    # GET /api/v1/reachability/{symbol_id} - why is this alive?
    @router.get("/reachability/{symbol_id}")
    def reachability(symbol_id: str) -> dict[str, Any]:
        """Explain the verdict for one symbol.

        This is the endpoint that makes the feature auditable, and the one an
        agent should consult before deleting anything: it names the mechanism
        that reached the symbol, the symbol it was reached through, and the trust
        tier of that mechanism.
        """
        try:
            index = load_index(project)
            graph = index.graph
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

        if symbol_id not in graph.symbols:
            raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol_id}")

        candidate = compute_reachability(graph, index.config).explain(symbol_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail=f"No verdict for {symbol_id}")

        return {
            "id": symbol_id,
            "verdict": candidate.verdict,
            "scope": candidate.scope,
            "reason": candidate.reason,
            "rescue": (
                {
                    "mechanism": candidate.rescue.name,
                    "tier": candidate.rescue.evidence.tier,
                    "detail": candidate.rescue.detail,
                    "via_symbol": candidate.rescue.via_symbol,
                }
                if candidate.rescue
                else None
            ),
        }

    # POST /api/v1/analyze - trigger analysis (protected)
    @router.post("/analyze")
    def trigger_analyze(api_key_check: None = Depends(verify_api_key)) -> dict[str, Any]:
        """Trigger analysis and return report summary."""
        config = ProjectConfig.load(project)
        graph, report = analyze(project, config)

        # Persist the graph we just computed. Calling cli.build() here would run the
        # whole analysis a second time.
        from .cli import write_artifacts

        write_artifacts(project, config, graph, report, quiet=True)
        invalidate(project)

        return {
            "status": "success",
            "files_scanned": report.files_scanned,
            "symbols_found": report.symbols_found,
            "relations_found": report.relations_found,
            "failures": len(report.failures),
            "warnings": len(report.warnings),
        }

    return router
