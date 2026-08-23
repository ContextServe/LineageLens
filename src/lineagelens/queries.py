"""Single source of truth for code graph queries.

This module centralizes all graph traversal logic used by GraphQL, REST, and MCP.
No duplicated query logic across the three API surfaces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import ProjectConfig
from .model import (
    SCHEMA_VERSION,
    CodeGraph,
    Container,
    Evidence,
    Relation,
    ResiliencySignal,
    Symbol,
)
from .report import AnalysisReport, FileFailure, SymbolWarning

if TYPE_CHECKING:
    from .index import GraphIndex
    from .reachability import DeadCodeCandidate


class GraphNotFoundError(RuntimeError):
    """Graph file not found or couldn't be loaded."""



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

    found = raw.get("schema_version", 1)
    if found != SCHEMA_VERSION:
        raise GraphNotFoundError(
            f"{path} was written by a different LineageLens graph schema "
            f"(found v{found}, expected v{SCHEMA_VERSION}). Symbol ids are not "
            f"comparable across versions.\nRe-run: lineagelens analyze {project}"
        )

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

    def _load_evidence(ev_raw: dict | None, default: Evidence) -> Evidence:
        if not ev_raw:
            return default
        return Evidence(
            tier=ev_raw.get("tier", default.tier),
            label=ev_raw.get("label", default.label),
            confidence=ev_raw.get("confidence"),
        )

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
            bases=s_raw.get("bases", []),
            entry_point_kinds=list(
                s_raw.get("entry_point_kinds")
                or ([s_raw["entry_point"]] if s_raw.get("entry_point") else [])
            ),
            is_abstract=s_raw.get("is_abstract", False),
            resiliency=[
                ResiliencySignal(
                    category=sig["category"],
                    severity=sig["severity"],
                    evidence=_load_evidence(
                        sig.get("evidence") if isinstance(sig.get("evidence"), dict) else None,
                        Evidence.from_legacy(sig["evidence"]) if isinstance(sig.get("evidence"), str)
                        else Evidence(tier="deterministic_heuristic", label="unknown"),
                    ),
                    line=sig.get("line", 0),
                )
                for sig in s_raw.get("resiliency", [])
            ],
        )
        graph.add_symbol(symbol)

    # Reconstruct relations
    for r_raw in raw.get("relations", []):
        relation = Relation(
            source=r_raw["source"],
            target=r_raw["target"],
            kind=r_raw["kind"],
            file=r_raw["file"],
            line=r_raw["line"],
            evidence=_load_evidence(r_raw.get("evidence"), Evidence(tier="deterministic_fact", label="static_ast")),
            resolution=r_raw.get("resolution", "resolved"),
            resolution_evidence=_load_evidence(r_raw.get("resolution_evidence"), Evidence(tier="deterministic_fact", label="static_scope_walk")),
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
            # Without these two, a round-tripped report is always is_clean() and
            # `lineagelens analyze --strict` cannot see the failures it just wrote.
            failures=[
                FileFailure(
                    file=f["file"],
                    stage=f["stage"],
                    error_type=f["error_type"],
                    message=f["message"],
                    line=f.get("line"),
                )
                for f in raw.get("failures", [])
            ],
            warnings=[
                SymbolWarning(
                    symbol_id=w.get("symbol_id"),
                    file=w["file"],
                    line=w["line"],
                    message=w["message"],
                    stage=w["stage"],
                )
                for w in raw.get("warnings", [])
            ],
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


def get_callers(
    graph: CodeGraph, symbol_id: str, index: GraphIndex | None = None
) -> list[Relation]:
    """Relations where this symbol is the target (things that reference it).

    Pass a :class:`~lineagelens.index.GraphIndex` to avoid a linear scan; without
    one this is O(#relations), which is why the metrics pass used to be O(V*E).
    """
    if index is not None:
        return index.callers_of(symbol_id)
    return [rel for rel in graph.relations if rel.target == symbol_id]


def get_callees(
    graph: CodeGraph, symbol_id: str, index: GraphIndex | None = None
) -> list[Relation]:
    """Relations where this symbol is the source (things it references)."""
    if index is not None:
        return index.callees_of(symbol_id)
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


def is_test_path(file_or_id: str | None, test_roots: tuple[str, ...] = ("tests",)) -> bool:
    """Check if a file or container id is under a test root.

    Args:
        file_or_id: Root-relative file path (e.g., "tests/test_foo.py") or container id (e.g., "app.tests.utils")
        test_roots: Tuple of test root directory names (default: ("tests",))

    Returns:
        True if the path/id starts with any test root
    """
    if not file_or_id:
        return False

    # File path format: "tests/test_foo.py" or "tests/utils.py"
    if "/" in file_or_id:
        for root in test_roots:
            if file_or_id.startswith(root + "/") or file_or_id == root:
                return True

    # Container id format: "app.tests" or "myproject.tests.helpers"
    # A container is test-related if its module path includes a test root directory
    else:
        parts = file_or_id.split(".")
        for _index, part in enumerate(parts):
            if part in test_roots:
                return True

    return False


def list_unreferenced_symbols(
    graph: CodeGraph, config: ProjectConfig | None = None
) -> list[DeadCodeCandidate]:
    """Symbols that warrant attention as possible dead code.

    Delegates to :mod:`lineagelens.reachability`. The previous implementation asked
    "does any relation target this symbol?", which treated a graph of call edges as
    a model of reachability. On a real project that was wrong 71% of the time,
    because a Pydantic model referenced only from a route signature, a dependency
    handed to Depends(), a base class and a name in __all__ are all live and none
    are called.

    Returns only symbols with an actionable verdict -- ``dead``, ``probably_dead``
    or ``test_only``. Use :func:`lineagelens.reachability.compute_reachability`
    directly for a verdict on every symbol, including why the rest are alive.

    Args:
        graph: CodeGraph
        config: ProjectConfig; loaded defaults are used if omitted

    Returns:
        Candidates, most severe first
    """
    from .reachability import compute_reachability

    return compute_reachability(graph, config).candidates()


def find_duplicate_names(graph: CodeGraph) -> set[tuple[str, str]]:
    """Find symbols that share the same name *and* kind in different modules.

    This is a "same name in multiple places" heuristic, not a body-hash duplicate
    check. Useful for spotting naming conflicts and accidental copy-paste.

    Returns ``(kind, name)`` pairs rather than bare names. Grouping by kind and then
    discarding it made a name duplicated across two methods also flag an unrelated
    class of the same name.

    Args:
        graph: CodeGraph

    Returns:
        Set of ``(kind, name)`` pairs that occur more than once
    """
    name_counts: dict[tuple[str, str], int] = {}
    for symbol in graph.symbols.values():
        key = (symbol.kind, symbol.name)
        name_counts[key] = name_counts.get(key, 0) + 1

    return {key for key, count in name_counts.items() if count > 1}


def get_module_dependencies(graph: CodeGraph) -> dict[str, set[str]]:
    """Build module-to-module dependency graph.

    Shows which modules depend on which other modules based on symbol calls.

    Args:
        graph: CodeGraph

    Returns:
        Dict mapping module_id -> set of module_ids it depends on
    """
    dependencies: dict[str, set[str]] = {}

    for relation in graph.relations:
        source_sym = graph.symbols.get(relation.source)
        target_sym = graph.symbols.get(relation.target)

        if not source_sym or not target_sym:
            continue

        source_module = source_sym.module
        target_module = target_sym.module

        # Don't include self-dependencies
        if source_module == target_module:
            continue

        if source_module not in dependencies:
            dependencies[source_module] = set()

        dependencies[source_module].add(target_module)

    return dependencies


def get_codebase_metrics(
    graph: CodeGraph, index: GraphIndex | None = None
) -> dict[str, Any]:
    """Get aggregate codebase metrics and statistics.

    Args:
        graph: CodeGraph
        index: Optional GraphIndex; supplying one avoids rebuilding adjacency

    Returns:
        Dict with various metrics about the codebase
    """
    unreferenced = list_unreferenced_symbols(graph)
    duplicates = find_duplicate_names(graph)
    entry_points = list_entry_points(graph)
    risks = list_resiliency_risks(graph)

    # Call depth, in one O(V+E) pass. This previously ran a transitive walk per
    # symbol -- O(V*E) -- and reported len(lineage), i.e. the size of the reachable
    # set, not a depth at all.
    from .index import index_for

    # Bound to a name rather than written as `(index or index_for(graph)).x`:
    # a parenthesised expression is not a resolvable receiver, so the attribute
    # access produced no edge and LineageLens reported its own max_call_chain --
    # and everything that only it reaches -- as dead.
    resolved_index = index if index is not None else index_for(graph)
    chains = resolved_index.max_call_chain
    max_depth = max(chains.values(), default=0)
    avg_depth = (sum(chains.values()) / len(chains)) if chains else 0

    # Risk distribution by category
    risk_by_category: dict[str, int] = {}
    risk_by_severity: dict[str, int] = {}
    for risk in risks:
        category = risk.get("category", "unknown")
        severity = risk.get("severity", "unknown")
        risk_by_category[category] = risk_by_category.get(category, 0) + 1
        risk_by_severity[severity] = risk_by_severity.get(severity, 0) + 1

    # Entry points by type
    entry_by_type: dict[str, int] = {}
    for ep in entry_points:
        ep_type = ep.entry_point or "unknown"
        entry_by_type[ep_type] = entry_by_type.get(ep_type, 0) + 1

    by_verdict: dict[str, int] = {}
    for candidate in unreferenced:
        by_verdict[candidate.verdict] = by_verdict.get(candidate.verdict, 0) + 1

    return {
        "total_symbols": len(graph.symbols),
        "total_containers": len(graph.containers),
        "total_relations": len(graph.relations),
        "total_entry_points": len(entry_points),
        "entry_points_by_type": entry_by_type,
        "dead_code": {
            "count": len(unreferenced),
            "by_verdict": by_verdict,
            "percentage": round(100 * len(unreferenced) / len(graph.symbols), 2) if graph.symbols else 0,
            "dead_percentage": (
                round(100 * by_verdict.get("dead", 0) / len(graph.symbols), 2)
                if graph.symbols
                else 0
            ),
        },
        "duplicates": {
            "count": len(duplicates),
            "total_occurrences": sum(
                len([s for s in graph.symbols.values() if (s.kind, s.name) == key]) for key in duplicates
            ),
        },
        "risks": {
            "total": len(risks),
            "by_category": risk_by_category,
            "by_severity": risk_by_severity,
        },
        "call_depth": {
            # Longest chain of CALLS edges, with cycles cut at the point of
            # recursion -- so this is a lower bound for mutually recursive code.
            "max_chain": max_depth,
            "average_chain": round(avg_depth, 2),
        },
        "modules": len({s.module for s in graph.symbols.values()}),
    }
