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
    get_callees,
    get_callers,
    get_lineage,
    get_module_overview,
    get_symbol,
    impact_analysis,
    list_entry_points,
    list_resiliency_risks,
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
        """
        try:
            graph = load_graph(project)
        except GraphNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        # Collect nodes
        nodes = []
        for symbol in graph.symbols.values():
            # Skip if filtering by module and this symbol's parent doesn't match
            if module and symbol.parent != module:
                continue

            has_risk = len(symbol.resiliency) > 0
            node = NodeView(
                id=symbol.id,
                label=symbol.name,
                kind=symbol.kind,
                parent=symbol.parent,
                entry_point=symbol.entry_point,
                async_=symbol.async_,
                has_resiliency_flag=has_risk,
            )
            nodes.append(node)

        # Collect edges
        edges = []
        for i, rel in enumerate(graph.relations):
            # Skip if filtering and endpoints not in scope
            if module:
                source_ok = any(s.id == rel.source for s in graph.symbols.values() if s.parent == module)
                target_ok = any(s.id == rel.target for s in graph.symbols.values() if s.parent == module)
                if not (source_ok or target_ok):
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

        # Write results
        from .cli import build, graph_path, report_path

        build(project)

        return {
            "status": "success",
            "files_scanned": report.files_scanned,
            "symbols_found": report.symbols_found,
            "relations_found": report.relations_found,
            "failures": len(report.failures),
            "warnings": len(report.warnings),
        }

    return router
