"""MCP (Model Context Protocol) server for Claude integration.

Exposes LineageLens queries as MCP tools so Claude can understand large codebases
without reading full source, reducing token cost and enabling safe impact analysis.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .analyzer import analyze
from .config import ProjectConfig
from .index import invalidate, load_index
from .queries import (
    GraphNotFoundError,
)
from .queries import (
    find_duplicate_names as query_find_duplicates,
)
from .queries import (
    get_callees as query_get_callees,
)
from .queries import (
    get_callers as query_get_callers,
)
from .queries import (
    get_codebase_metrics as query_get_metrics,
)
from .queries import (
    get_lineage as query_get_lineage,
)
from .queries import (
    get_module_dependencies as query_get_module_deps,
)
from .queries import (
    get_module_overview as query_get_module_overview,
)
from .queries import (
    get_symbol as query_get_symbol,
)
from .queries import (
    impact_analysis as query_impact_analysis,
)
from .queries import (
    list_entry_points as query_list_entry_points,
)
from .queries import (
    list_resiliency_risks as query_list_resiliency_risks,
)
from .queries import (
    search_symbols as query_search_symbols,
)
from .reachability import compute_reachability

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Get project path from environment
PROJECT_PATH = Path(os.environ.get("LINEAGELENS_PROJECT", "."))

def get_cached_index(project: Path) -> Any:
    """Load the indexed graph, reusing it while graph.json is unchanged."""
    return load_index(project)


def get_cached_graph(project: Path) -> Any:
    """Load the graph, reusing it while graph.json is unchanged.

    Thin wrapper over the shared index cache so the tool bodies below read
    naturally; use :func:`get_cached_index` where adjacency lookups are needed.
    """
    return load_index(project).graph


def create_mcp_server() -> MCPServer:
    """Create and configure the MCP server."""
    server = MCPServer("lineagelens")

    @server.tool()
    async def get_symbol(symbol_id: str, **kwargs: Any) -> dict[str, Any]:
        """Get full details for a symbol by ID.

        Args:
            symbol_id: Symbol ID (e.g., 'app.api.fetch_user')

        Returns:
            Symbol details: id, kind, name, file, line, end_line, module, entry_point,
            async_, description, inputs, outputs, decorators
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            sym = query_get_symbol(graph, symbol_id)
            if not sym:
                return {"error": f"Symbol not found: {symbol_id}"}
            return asdict(sym)
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def search_symbols(
        text: str, kind: str | None = None, entry_point: str | None = None, limit: int = 30, **kwargs: Any
    ) -> dict[str, Any]:
        """Search symbols by name or ID substring.

        Args:
            text: Search term (case-insensitive)
            kind: Filter by kind (class, function, method)
            entry_point: Filter by entry point type (api_route, cli_command, test, framework_callback)
            limit: Max results to return

        Returns:
            List of matching symbols
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            results = query_search_symbols(graph, text, kind=kind, limit=limit)

            # Filter by entry_point if specified
            if entry_point:
                results = [s for s in results if s.entry_point == entry_point]

            return {"count": len(results), "symbols": [asdict(s) for s in results]}
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_callers(symbol_id: str, **kwargs: Any) -> dict[str, Any]:
        """Find all symbols that call this one.

        Args:
            symbol_id: Symbol ID

        Returns:
            List of relations where this symbol is the target
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            relations = query_get_callers(graph, symbol_id, get_cached_index(PROJECT_PATH))
            return {"symbol_id": symbol_id, "callers": [asdict(r) for r in relations]}
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_callees(symbol_id: str, **kwargs: Any) -> dict[str, Any]:
        """Find all symbols this one calls.

        Args:
            symbol_id: Symbol ID

        Returns:
            List of relations where this symbol is the source
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            relations = query_get_callees(graph, symbol_id, get_cached_index(PROJECT_PATH))
            return {"symbol_id": symbol_id, "callees": [asdict(r) for r in relations]}
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_lineage(
        symbol_id: str, direction: str = "forward", max_depth: int = 5, **kwargs: Any
    ) -> dict[str, Any]:
        """Get transitive call path (forward or backward).

        Args:
            symbol_id: Symbol ID
            direction: Direction of traversal (forward, backward, both)
            max_depth: Maximum traversal depth

        Returns:
            List of reachable symbols and their depths
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            steps = query_get_lineage(graph, symbol_id, direction=direction, max_depth=max_depth)
            return {
                "symbol_id": symbol_id,
                "direction": direction,
                "depth": len(steps),
                "steps": [asdict(s) for s in steps],
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_impact(symbol_id: str, max_depth: int = 10, **kwargs: Any) -> dict[str, Any]:
        """Analyze impact of changes to a symbol (backward transitive closure).

        Args:
            symbol_id: Symbol ID
            max_depth: Maximum traversal depth

        Returns:
            Symbols affected by changes to this one
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            report = query_impact_analysis(graph, symbol_id, max_depth=max_depth)
            return {
                "symbol_id": symbol_id,
                "affected_count": len(report.affected),
                "affected_entry_points": report.affected_entry_points,
                "affected": [asdict(s) for s in report.affected],
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_module_overview(module: str, **kwargs: Any) -> dict[str, Any]:
        """Get high-level overview of a module.

        Args:
            module: Module ID (e.g., 'app.api')

        Returns:
            Module symbols and submodules
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            overview = query_get_module_overview(graph, module)
            if not overview:
                return {"error": f"Module not found: {module}"}
            return {
                "module_id": overview.module_id,
                "symbol_count": len(overview.symbols),
                "submodule_count": len(overview.submodules),
                "symbols": overview.symbols,
                "submodules": overview.submodules,
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def list_entry_points(kind: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """List all entry points (API routes, CLI commands, tests).

        Args:
            kind: Filter by kind (api_route, cli_command, test, framework_callback)

        Returns:
            List of entry point symbols
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            entries = query_list_entry_points(graph, kind=kind)
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
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def list_risks(min_severity: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """List all resiliency and risk signals.

        Helps identify fragile or concerning code patterns.

        Args:
            min_severity: Minimum severity level (info, review, high)

        Returns:
            List of risk signals
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            risks = query_list_resiliency_risks(graph, min_severity=min_severity)
            return {"count": len(risks), "risks": risks}
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_project_config(**kwargs: Any) -> dict[str, Any]:
        """Get the project configuration.

        Returns the lineagelens.yaml configuration including source_roots, test_roots,
        frameworks, and analysis settings.

        Returns:
            Project configuration details
        """
        try:
            config = ProjectConfig.load(PROJECT_PATH)
            return {
                "source_roots": list(config.source_roots),
                "test_roots": list(config.test_roots),
                "script_roots": list(config.script_roots),
                "frameworks": list(config.frameworks),
                "output_directory": config.output.directory,
                "output_filename": config.output.filename,
                "server_host": config.server.host,
                "server_port": config.server.port,
            }
        except Exception as e:
            return {"error": str(e)}

    @server.tool()
    async def get_metrics(**kwargs: Any) -> dict[str, Any]:
        """Get aggregate codebase metrics and statistics.

        Returns overall health metrics including:
        - Total symbols, containers, relations
        - Dead code count/percentage, broken down by confidence (confirmed vs unconfirmed_possible_dynamic_dispatch)
        - Duplicate name count
        - Risk distribution by category and severity
        - Call depth statistics
        - Entry points by type

        Returns:
            Comprehensive codebase metrics
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            metrics = query_get_metrics(graph, get_cached_index(PROJECT_PATH))
            return metrics
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def find_entry_points(entry_point_type: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Find entry points filtered by type.

        Args:
            entry_point_type: Filter by type (api_route, cli_command, test, framework_callback)
                           If None, returns all entry points.

        Returns:
            Entry points of the specified type
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            entries = query_list_entry_points(graph, kind=entry_point_type)
            return {
                "filter_type": entry_point_type or "all",
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
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_module_dependencies(**kwargs: Any) -> dict[str, Any]:
        """Get module-to-module dependency graph.

        Shows which modules depend on which other modules based on symbol calls.

        Returns:
            Module dependency graph (module -> list of dependencies)
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            deps = query_get_module_deps(graph)
            return {
                "count": len(deps),
                "dependencies": {module: sorted(dep_set) for module, dep_set in deps.items()},
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def list_dead_code(
        verdict: str | None = None, scope: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Find code that is not reachable from any entry point.

        Reachability is computed by walking outward from entry points over every
        edge kind, not by asking "does anything call this". Calls alone do not
        model Python reachability: a request model referenced only from a route
        signature, a dependency handed to Depends(), a base class and a name in
        __all__ are all live and none are called.

        Verdicts, in descending severity:
          dead           not reachable by any modelled mechanism, and no
                         same-named unresolved call site exists
          probably_dead  unreachable, but an unresolved call shares its name, so
                         deadness cannot be asserted
          test_only      reachable only from tests -- deleting it breaks the
                         suite, but nothing shipped uses it

        Symbols that are alive, dynamic_only or public_api are not returned here.
        Call get_reachability on any symbol to see why it is considered alive.

        IMPORTANT: static analysis cannot see reflection, config-driven dispatch
        or plugin loading. A `dead` verdict means "no static reference exists",
        not "unused at runtime". Check before deleting, and record the answer with
        a `# lineagelens: keep` comment if the symbol is reached dynamically.

        Args:
            verdict: Filter to one verdict (dead, probably_dead, test_only)
            scope: Filter by "source" or "test"

        Returns:
            Candidates with a verdict and reason each, most severe first
        """
        try:
            index = get_cached_index(PROJECT_PATH)
            result = compute_reachability(index.graph, index.config)
            candidates = result.candidates()
            if verdict:
                candidates = [c for c in candidates if c.verdict == verdict]
            if scope:
                candidates = [c for c in candidates if c.scope == scope]
            return {
                "count": len(candidates),
                "by_verdict": result.by_verdict(),
                "dead_code_candidates": [
                    {
                        "id": c.symbol.id,
                        "kind": c.symbol.kind,
                        "name": c.symbol.name,
                        "file": c.symbol.file,
                        "line": c.symbol.line,
                        "module": c.symbol.module,
                        "description": c.symbol.description,
                        "verdict": c.verdict,
                        "scope": c.scope,
                        "reason": c.reason,
                    }
                    for c in candidates
                ],
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_reachability(symbol_id: str, **kwargs: Any) -> dict[str, Any]:
        """Explain why a symbol is considered reachable, or why it is not.

        Consult this before deleting anything. It names the mechanism that reached
        the symbol, the symbol it was reached through, and that mechanism's trust
        tier -- deterministic_fact for something read off literal syntax,
        deterministic_heuristic for a name match such as type inference or a
        polymorphic override.

        Args:
            symbol_id: Fully qualified symbol id

        Returns:
            The verdict, the rescue mechanism, and the reasoning
        """
        try:
            index = get_cached_index(PROJECT_PATH)
            if symbol_id not in index.graph.symbols:
                return {"error": f"Symbol not found: {symbol_id}"}
            candidate = compute_reachability(index.graph, index.config).explain(symbol_id)
            if candidate is None:
                return {"error": f"No verdict for {symbol_id}"}
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
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def list_duplicate_names(**kwargs: Any) -> dict[str, Any]:
        """Find symbols with duplicate names across different modules.

        Returns symbols that share the same name and kind, which may indicate
        copy-paste code or naming conflicts.

        Returns:
            One entry per (kind, name) pair that appears more than once. A name
            duplicated across methods does not flag an unrelated class of the same
            name -- the kind is part of the grouping key.
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            duplicate_keys = query_find_duplicates(graph)

            duplicates_detail: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for sym in graph.symbols.values():
                key = (sym.kind, sym.name)
                if key in duplicate_keys:
                    duplicates_detail.setdefault(key, []).append(
                        {
                            "id": sym.id,
                            "kind": sym.kind,
                            "module": sym.module,
                            "file": sym.file,
                            "line": sym.line,
                        }
                    )

            return {
                "count": len(duplicate_keys),
                "duplicate_names": [
                    {
                        "kind": kind,
                        "name": name,
                        "occurrences": len(duplicates_detail.get((kind, name), [])),
                        "symbols": duplicates_detail.get((kind, name), []),
                    }
                    for kind, name in sorted(duplicate_keys)
                ],
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def trigger_analysis(**kwargs: Any) -> dict[str, Any]:
        """Trigger a fresh analysis of the project.

        Runs the analyzer and writes graph.json + report.json.

        Returns:
            Analysis report summary
        """
        try:
            from .cli import write_artifacts

            config = ProjectConfig.load(PROJECT_PATH)
            graph, report = analyze(PROJECT_PATH, config)

            # This previously skipped the write entirely, so popping the cache just
            # made the next read re-cache the *stale* file at an unchanged mtime.
            write_artifacts(PROJECT_PATH, config, graph, report, quiet=True)
            invalidate(PROJECT_PATH)

            return {
                "status": "success",
                "files_scanned": report.files_scanned,
                "symbols_found": report.symbols_found,
                "relations_found": report.relations_found,
                "containers_found": report.containers_found,
                "failures": len(report.failures),
                "warnings": len(report.warnings),
                "is_clean": report.is_clean(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return server


def main() -> None:
    """Entry point for the MCP server (lineagelens-mcp command)."""
    server = create_mcp_server()
    logger.info("Starting LineageLens MCP server on stdio")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
