"""Single source of truth for code graph queries.

This module centralizes all graph traversal logic used by GraphQL, REST, and MCP.
No duplicated query logic across the three API surfaces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import CodeGraph, Container, Relation, Symbol
from .report import AnalysisReport


class GraphNotFoundError(RuntimeError):
    """Graph file not found or couldn't be loaded."""

    pass


def load_graph(project: Path) -> CodeGraph:
    """Load a code graph from .lineagelens/graph.json.

    Raises:
        GraphNotFoundError: If graph file doesn't exist or is malformed
    """
    path = project / ".lineagelens" / "graph.json"
    if not path.exists():
        raise GraphNotFoundError(
            f"Analysis graph not found at {path}\n"
            f"Please run: lineagelens analyze {project}\n"
            f"Then retry: lineagelens serve {project}"
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GraphNotFoundError(f"Failed to load graph: {e}") from e

    # Deserialize back into CodeGraph with proper types
    # (This is a simplified version; production would use Pydantic or similar)
    graph = CodeGraph(project_root=raw.get("project_root", str(project)))

    # Reconstruct containers
    for c_raw in raw.get("containers", []):
        container = Container(
            id=c_raw["id"],
            kind=c_raw["kind"],
            name=c_raw["name"],
            file=c_raw.get("file"),
            parent=c_raw.get("parent"),
            children=c_raw.get("children", []),
            docstring=c_raw.get("docstring"),
        )
        graph.add_container(container)

    # Reconstruct symbols
    for s_raw in raw.get("symbols", []):
        symbol = Symbol(
            id=s_raw["id"],
            kind=s_raw["kind"],
            name=s_raw["name"],
            file=s_raw["file"],
            line=s_raw["line"],
            module=s_raw["module"],
            parent=s_raw.get("parent"),
            end_line=s_raw.get("end_line"),
            async_=s_raw.get("async_", False),
            description=s_raw.get("description"),
            inputs=s_raw.get("inputs", []),
            outputs=s_raw.get("outputs", []),
            decorators=s_raw.get("decorators", []),
            entry_point=s_raw.get("entry_point"),
        )
        # Note: resiliency signals would be reconstructed here too
        graph.add_symbol(symbol)

    # Reconstruct relations (simple for now, doesn't reconstruct Evidence objects)
    for r_raw in raw.get("relations", []):
        relation = Relation(
            source=r_raw["source"],
            target=r_raw["target"],
            kind=r_raw["kind"],
            file=r_raw["file"],
            line=r_raw["line"],
            resolution=r_raw.get("resolution", "resolved"),
            arguments=r_raw.get("arguments", []),
        )
        graph.add_relation(relation)

    return graph


def load_report(project: Path) -> AnalysisReport | None:
    """Load an analysis report if it exists.

    Returns None if report doesn't exist (which is fine, it's optional).
    """
    path = project / ".lineagelens" / "report.json"
    if not path.exists():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Deserialize into AnalysisReport
        # (simplified; production uses proper deserialization)
        return AnalysisReport(
            project_root=raw.get("project_root", ""),
            started_at=raw.get("started_at", ""),
            finished_at=raw.get("finished_at", ""),
            files_scanned=raw.get("files_scanned", 0),
            files_skipped=raw.get("files_skipped", 0),
            symbols_found=raw.get("symbols_found", 0),
            relations_found=raw.get("relations_found", 0),
            containers_found=raw.get("containers_found", 0),
        )
    except (OSError, json.JSONDecodeError):
        return None


def get_symbol(graph: CodeGraph, symbol_id: str) -> Symbol | None:
    """Get a symbol by ID."""
    return graph.symbols.get(symbol_id)


def search_symbols(
    graph: CodeGraph,
    text: str,
    *,
    kind: str | None = None,
    entry_point: str | None = None,
    limit: int = 30,
) -> list[Symbol]:
    """Search symbols by name/id substring.

    Args:
        graph: CodeGraph to search
        text: Search term (case-insensitive substring match)
        kind: Filter by symbol kind (class, function, method)
        entry_point: Filter by entry_point (api_route, cli_command, etc.)
        limit: Max results to return

    Returns:
        List of matching symbols
    """
    term = text.lower()
    results = []

    for symbol in graph.symbols.values():
        # Filter by kind if specified
        if kind and symbol.kind != kind:
            continue
        # Filter by entry_point if specified
        if entry_point and symbol.entry_point != entry_point:
            continue
        # Match against symbol id or name
        if term in symbol.id.lower() or term in symbol.name.lower():
            results.append(symbol)
            if len(results) >= limit:
                break

    return results


def get_callers(graph: CodeGraph, symbol_id: str) -> list[Relation]:
    """Get all relations where this symbol is the target (things that call it)."""
    return [rel for rel in graph.relations if rel.target == symbol_id]


def get_callees(graph: CodeGraph, symbol_id: str) -> list[Relation]:
    """Get all relations where this symbol is the source (things it calls)."""
    return [rel for rel in graph.relations if rel.source == symbol_id]


@dataclass(frozen=True)
class LineageStep:
    """A step in a lineage (transitive call chain)."""

    symbol_id: str
    relation_kind: str
    depth: int
    resolution: str


def get_lineage(
    graph: CodeGraph,
    symbol_id: str,
    *,
    direction: str = "forward",
    max_depth: int = 5,
) -> list[LineageStep]:
    """Get transitive call path (forward or backward).

    Args:
        graph: CodeGraph
        symbol_id: Starting symbol ID
        direction: "forward" (callees), "backward" (callers), or "both"
        max_depth: Max recursion depth to prevent infinite loops

    Returns:
        List of LineageStep objects representing the call path
    """
    visited = {symbol_id}
    steps: list[LineageStep] = []

    def bfs(current_id: str, depth: int, is_forward: bool) -> None:
        if depth >= max_depth:
            return

        if is_forward:
            relations = get_callees(graph, current_id)
        else:
            relations = get_callers(graph, current_id)

        for rel in relations:
            next_id = rel.target if is_forward else rel.source
            if next_id not in visited:
                visited.add(next_id)
                steps.append(LineageStep(symbol_id=next_id, relation_kind=rel.kind, depth=depth + 1, resolution=rel.resolution))
                bfs(next_id, depth + 1, is_forward)

    if direction in ("forward", "both"):
        bfs(symbol_id, 0, True)
    if direction in ("backward", "both"):
        bfs(symbol_id, 0, False)

    return steps


@dataclass(frozen=True)
class ImpactReport:
    """Result of impact analysis (what breaks if I change this?)."""

    symbol_id: str
    affected: list[LineageStep]
    affected_entry_points: list[str]  # Reachable entry points that would be affected


def impact_analysis(graph: CodeGraph, symbol_id: str, *, max_depth: int = 10) -> ImpactReport:
    """Backward transitive closure: what calls (directly or transitively) this symbol?

    Identifies the blast radius of changes to this symbol.

    Args:
        graph: CodeGraph
        symbol_id: Symbol to analyze
        max_depth: Max recursion depth

    Returns:
        ImpactReport with all affected symbols and entry points
    """
    affected_steps = get_lineage(graph, symbol_id, direction="backward", max_depth=max_depth)

    # Find which affected symbols are entry points
    affected_entry_points = []
    for step in affected_steps:
        symbol = get_symbol(graph, step.symbol_id)
        if symbol and symbol.entry_point:
            affected_entry_points.append(f"{step.symbol_id} ({symbol.entry_point})")

    return ImpactReport(symbol_id=symbol_id, affected=affected_steps, affected_entry_points=affected_entry_points)


@dataclass(frozen=True)
class ModuleOverview:
    """Shallow overview of a module (for agent queries without full source load)."""

    module_id: str
    container: Container | None
    symbols: list[dict[str, Any]]  # Shallow: id, kind, name, entry_point, description only
    submodules: list[str]  # Child module container IDs


def get_module_overview(graph: CodeGraph, module: str) -> ModuleOverview | None:
    """Get a high-level overview of a module without loading full symbol details.

    Useful for agents to understand module structure before diving into specifics.
    """
    # Find the container for this module
    container = graph.containers.get(module)
    if not container:
        return None

    # Collect symbols defined directly in this module (not in nested classes/functions)
    symbols_in_module = [
        {
            "id": sym.id,
            "kind": sym.kind,
            "name": sym.name,
            "entry_point": sym.entry_point,
            "description": sym.description or "(no docstring)",
        }
        for sym in graph.symbols.values()
        if sym.parent == module
    ]

    # Find direct child modules
    submodules = [cid for cid in container.children if cid in graph.containers and graph.containers[cid].kind == "module"]

    return ModuleOverview(module_id=module, container=container, symbols=symbols_in_module, submodules=submodules)


def list_entry_points(graph: CodeGraph, *, kind: str | None = None) -> list[Symbol]:
    """List all entry points (API routes, CLI commands, tests).

    Args:
        graph: CodeGraph
        kind: Filter by entry_point kind (api_route, cli_command, test, etc.)

    Returns:
        List of symbols marked as entry points
    """
    results = [sym for sym in graph.symbols.values() if sym.entry_point]

    if kind:
        results = [sym for sym in results if sym.entry_point == kind]

    return results


def list_resiliency_risks(graph: CodeGraph, *, min_severity: str | None = None) -> list[dict[str, Any]]:
    """List all resiliency/risk signals across the codebase.

    Args:
        graph: CodeGraph
        min_severity: Filter by minimum severity (info, review, high)

    Returns:
        List of {symbol_id, category, severity, evidence, line} dicts
    """
    severity_order = {"info": 0, "review": 1, "high": 2}
    min_level = severity_order.get(min_severity or "info", 0)

    results = []
    for symbol in graph.symbols.values():
        for signal in symbol.resiliency:
            if severity_order.get(signal.severity, 0) >= min_level:
                results.append(
                    {
                        "symbol_id": symbol.id,
                        "category": signal.category,
                        "severity": signal.severity,
                        "evidence": signal.evidence.label,
                        "line": signal.line,
                    }
                )

    return results
