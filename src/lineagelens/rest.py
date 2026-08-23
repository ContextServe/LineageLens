"""REST API for LineageLens (shared by web frontend and ChatGPT Actions).

Provides both read-only queries and a trigger for analysis.
Uses queries.py as the single source of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel

from .analyzer import analyze
from .config import ProjectConfig
from .queries import (
    GraphNotFoundError,
    find_duplicate_names,
    get_callees,
    get_callers,
    get_lineage,
    get_module_overview,
    get_symbol,
    impact_analysis,
    is_test_path,
    list_entry_points,
    list_resiliency_risks,
    list_unreferenced_symbols,
    load_graph,
    search_symbols,
)


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
    possibly_dead: bool = False  # Backward compat: true if any dead-code confidence
    dead_code_confidence: str | None = None  # "confirmed" | "unconfirmed_possible_dynamic_dispatch" | None
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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        # Load config to get test_roots
        config = ProjectConfig.load(project)

        # Precompute unreferenced symbols and duplicate names for efficiency
        unreferenced = list_unreferenced_symbols(graph)
        dead_code_confidence = {c.symbol.id: c.confidence for c in unreferenced}
        duplicate_names = find_duplicate_names(graph)

        # Collect nodes
        nodes = []
        rendered_node_ids = set()  # Track which nodes we're actually rendering

        # First pass: emit Container nodes (packages and modules)
        for container in graph.containers.values():
            # Skip if filtering by module and this container isn't in/under that module
            if module:
                # Container is in scope if its id matches or starts with the module id
                if container.id != module and not container.id.startswith(module + "."):
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
                possibly_dead=False,
                dead_code_confidence=None,
                duplicate_name=False,
            )
            nodes.append(node)
            rendered_node_ids.add(container.id)

        # Second pass: emit Symbol nodes
        for symbol in graph.symbols.values():
            # Skip if filtering by module and this symbol's parent doesn't match
            if module:
                if symbol.parent != module and not (symbol.parent and symbol.parent.startswith(module + ".")):
                    continue

            has_risk = len(symbol.resiliency) > 0
            is_test = is_test_path(symbol.file, test_roots=config.test_roots)
            dead_confidence = dead_code_confidence.get(symbol.id)
            possibly_dead = dead_confidence is not None
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
                possibly_dead=possibly_dead,
                dead_code_confidence=dead_confidence,
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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        relations = get_callers(graph, symbol_id)
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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        relations = get_callees(graph, symbol_id)
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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

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
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        risks = list_resiliency_risks(graph, min_severity=min_severity)
        return [RiskOut(**r) for r in risks]

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

        return {
            "status": "success",
            "files_scanned": report.files_scanned,
            "symbols_found": report.symbols_found,
            "relations_found": report.relations_found,
            "failures": len(report.failures),
            "warnings": len(report.warnings),
        }

    return router
