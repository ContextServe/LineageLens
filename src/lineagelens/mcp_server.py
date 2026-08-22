"""MCP (Model Context Protocol) server for Claude integration.

Exposes LineageLens queries as MCP tools so Claude can understand large codebases
without reading full source, reducing token cost and enabling safe impact analysis.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

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

logger = logging.getLogger(__name__)

# Global mtime cache to avoid re-parsing graph.json on every call
_graph_cache: dict[str, tuple[float, Any]] = {}


def get_cached_graph(project: Path) -> Any:
    """Load graph with mtime-based caching."""
    graph_path = project / ".lineagelens" / "graph.json"
    mtime = graph_path.stat().st_mtime if graph_path.exists() else 0

    if project in _graph_cache:
        cached_mtime, cached_graph = _graph_cache[project]
        if cached_mtime == mtime:
            return cached_graph

    graph = load_graph(project)
    _graph_cache[project] = (mtime, graph)
    return graph


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server."""
    mcp = FastMCP("lineagelens")

    @mcp.tool()
    def get_symbol(project: str, symbol_id: str) -> dict[str, Any]:
        """Get full details for a symbol by ID.

        Args:
            project: Project root path
            symbol_id: Symbol ID (e.g., "app.api.fetch_user")

        Returns:
            Symbol details: id, kind, name, file, line, end_line, module, entry_point,
            async_, description, inputs, outputs, decorators
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        sym = get_symbol(graph, symbol_id)
        if not sym:
            return {"error": f"Symbol not found: {symbol_id}"}

        return asdict(sym)

    @mcp.tool()
    def search_symbols(project: str, text: str, kind: str | None = None, limit: int = 30) -> dict[str, Any]:
        """Search symbols by name or ID substring.

        Args:
            project: Project root path
            text: Search term (case-insensitive)
            kind: Filter by kind (class, function, method)
            limit: Max results to return

        Returns:
            List of matching symbols
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        results = search_symbols(graph, text, kind=kind, limit=limit)
        return {"count": len(results), "symbols": [asdict(s) for s in results]}

    @mcp.tool()
    def get_callers(project: str, symbol_id: str) -> dict[str, Any]:
        """Find all symbols that call this one.

        Args:
            project: Project root path
            symbol_id: Symbol ID

        Returns:
            List of relations where this symbol is the target
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        relations = get_callers(graph, symbol_id)
        return {
            "symbol_id": symbol_id,
            "callers": [asdict(r) for r in relations],
        }

    @mcp.tool()
    def get_callees(project: str, symbol_id: str) -> dict[str, Any]:
        """Find all symbols this one calls.

        Args:
            project: Project root path
            symbol_id: Symbol ID

        Returns:
            List of relations where this symbol is the source
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        relations = get_callees(graph, symbol_id)
        return {
            "symbol_id": symbol_id,
            "callees": [asdict(r) for r in relations],
        }

    @mcp.tool()
    def get_lineage(project: str, symbol_id: str, direction: str = "forward", max_depth: int = 5) -> dict[str, Any]:
        """Get transitive call path (forward or backward).

        Args:
            project: Project root path
            symbol_id: Symbol ID
            direction: "forward" (callees), "backward" (callers), "both"
            max_depth: Max recursion depth

        Returns:
            List of LineageStep objects
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        steps = get_lineage(graph, symbol_id, direction=direction, max_depth=max_depth)
        return {
            "symbol_id": symbol_id,
            "direction": direction,
            "lineage": [asdict(s) for s in steps],
        }

    @mcp.tool()
    def impact_analysis(project: str, symbol_id: str, max_depth: int = 10) -> dict[str, Any]:
        """Backward transitive closure: what would break if I change this symbol?

        Critical for safe refactoring. Shows all affected code and entry points.

        Args:
            project: Project root path
            symbol_id: Symbol ID to analyze
            max_depth: Max recursion depth

        Returns:
            ImpactReport with affected symbols and entry points
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        report = impact_analysis(graph, symbol_id, max_depth=max_depth)
        return {
            "symbol_id": symbol_id,
            "affected_count": len(report.affected),
            "affected": [asdict(s) for s in report.affected],
            "affected_entry_points": report.affected_entry_points,
        }

    @mcp.tool()
    def get_module_overview(project: str, module: str) -> dict[str, Any]:
        """Get high-level overview of a module (for safe exploration).

        This is the key tool for agents to understand module structure without
        reading full source files. Returns shallow symbol summaries + submodules.

        Args:
            project: Project root path
            module: Module ID (e.g., "app.api")

        Returns:
            ModuleOverview with symbols and submodules
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        overview = get_module_overview(graph, module)
        if not overview:
            return {"error": f"Module not found: {module}"}

        return {
            "module_id": overview.module_id,
            "symbol_count": len(overview.symbols),
            "symbols": overview.symbols,
            "submodules": overview.submodules,
        }

    @mcp.tool()
    def list_entry_points(project: str, kind: str | None = None) -> dict[str, Any]:
        """List all entry points (API routes, CLI commands, tests).

        Useful for understanding the public surface of the codebase.

        Args:
            project: Project root path
            kind: Filter by entry_point kind (api_route, cli_command, test)

        Returns:
            List of entry point symbols
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        entries = list_entry_points(graph, kind=kind)
        return {
            "count": len(entries),
            "entry_points": [
                {
                    "id": s.id,
                    "kind": s.kind,
                    "name": s.name,
                    "entry_point": s.entry_point,
                    "file": s.file,
                    "line": s.line,
                    "description": s.description,
                }
                for s in entries
            ],
        }

    @mcp.tool()
    def list_resiliency_risks(project: str, min_severity: str | None = None) -> dict[str, Any]:
        """List all resiliency and risk signals.

        Helps identify fragile or concerning code patterns.

        Args:
            project: Project root path
            min_severity: Filter by minimum severity (info, review, high)

        Returns:
            List of risk signals
        """
        try:
            graph = get_cached_graph(Path(project))
        except GraphNotFoundError as e:
            return {"error": str(e)}

        risks = list_resiliency_risks(graph, min_severity=min_severity)
        return {
            "count": len(risks),
            "risks": risks,
        }

    @mcp.tool()
    def trigger_analysis(project: str) -> dict[str, Any]:
        """Trigger a fresh analysis of the project.

        Runs the analyzer and writes graph.json + report.json.

        Args:
            project: Project root path

        Returns:
            Analysis report summary
        """
        try:
            config = ProjectConfig.load(Path(project))
            graph, report = analyze(Path(project), config)

            # Clear cache
            _graph_cache.pop(Path(project), None)

            return {
                "status": "success",
                "started_at": report.started_at,
                "finished_at": report.finished_at,
                "files_scanned": report.files_scanned,
                "files_skipped": report.files_skipped,
                "symbols_found": report.symbols_found,
                "relations_found": report.relations_found,
                "containers_found": report.containers_found,
                "failures": len(report.failures),
                "warnings": len(report.warnings),
                "is_clean": report.is_clean(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return mcp


def main() -> None:
    """Entry point for the MCP server (lineagelens-mcp command)."""
    mcp = create_mcp_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
