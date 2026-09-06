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


def _is_valid_type_name(name: str) -> bool:
    """Check if type name is properly formatted.

    Allows:
    - Simple names: ClassName
    - Qualified names: pkg.ClassName, pkg.sub.ClassName
    - Generic types: List<String>, Map<String, Integer>
    """
    if not name:
        return False

    # Remove allowed special chars and check if result is alphanumeric + dots
    cleaned = (
        name.replace(".", "")
        .replace("[", "")
        .replace("]", "")
        .replace("<", "")
        .replace(">", "")
        .replace(",", "")
        .replace(" ", "")
    )

    return cleaned.replace("_", "").isalnum()


def _type_matches(graph: Any, field_type: str, search_type: str, include_subtypes: bool) -> bool:
    """Check if field_type matches search_type (exact or subtype).

    Args:
        graph: CodeGraph instance
        field_type: Type of the field (from Symbol.fields)
        search_type: Type to search for
        include_subtypes: If true, also match subtypes via MRO

    Returns:
        True if the types match according to criteria
    """
    # Exact match
    if field_type == search_type:
        return True

    # Subtype match (if enabled)
    if include_subtypes:
        return _is_subtype_of(graph, field_type, search_type)

    return False


def _is_subtype_of(graph: Any, candidate: str, parent: str) -> bool:
    """Check if candidate is a subtype of parent (recursive MRO walk).

    Args:
        graph: CodeGraph instance
        candidate: Type to check
        parent: Parent type to check against

    Returns:
        True if candidate is a subclass of parent
    """
    if candidate == parent:
        return True

    candidate_sym = graph.symbols.get(candidate)
    if not candidate_sym:
        return False

    # Check base classes
    for base_name in candidate_sym.bases:
        # Try to resolve base class to symbol ID
        base_sym = graph.symbols.get(base_name)
        if base_sym and _is_subtype_of(graph, base_sym.id, parent):
            return True

    return False


def _get_ontology_instructions() -> str:
    """Get the ontology primer for the MCP server.

    This provides Claude agents with essential context about the code graph schema,
    relation kinds, evidence tiers, and query routing patterns. Sent during MCP
    initialization handshake so agents understand tool semantics without reverse-engineering.

    Returns:
        Ontology primer as a formatted string
    """
    return """# LineageLens Code Graph Ontology

## Relation Kinds

The code graph tracks five types of relations between symbols:

- **CALLS**: Direct invocation (method call, function call, constructor call)
- **INHERITS**: Class inheritance (extends) or interface implementation (implements)
- **OVERRIDES**: Method override in a subclass (dynamic dispatch target)
- **DECORATES**: Annotation/decorator applied to a symbol
- **PROVIDES**: SPI or service-provider registration (META-INF/services/*, META-INF/dubbo/internal/*)

When querying with `get_callers()` or `get_callees()`, results include all relation kinds above.
Filter by `relation.kind` to isolate specific patterns (e.g., kind="CALLS" to exclude INHERITS/DECORATES).

## Evidence Tiers & Resolution Status

Each relation has two metadata fields describing how certain the analysis is:

### Evidence Tier
- **deterministic_fact**: From literal syntax (observed directly, not inferred)
- **deterministic_heuristic**: From name/signature matching (e.g., polymorphic override detection)

### Resolution Status
- **resolved**: Symbol found directly in the graph
- **resolved_via_inference**: Symbol matched via name or type heuristic (e.g., interface implementation)
- **external_or_dynamic**: Analysis hit a limit. The symbol may exist but static analysis cannot see it.
  - Examples: reflection-based dispatch, SPI/plugin loading, runtime registration, config-driven routes
  - When you see this, DO NOT conclude "this is unknowable" — check for alternative relation chains

## Query Routing Guide

Use this table to pick the right tool for your intent:

| Intent | Tool(s) | Notes |
|--------|---------|-------|
| "Find all callers of function X" | `get_callers(X)` then filter `kind="CALLS"` | Includes INHERITS, DECORATES |
| "Find all implementations of interface I" | `get_callers(I)` then filter `kind="INHERITS"` | Complement with `list_implementations(I)` (Issue #38) |
| "Find all providers of service S" | `list_providers(S)` (Issue #38) | Extracts PROVIDES from SPI registries |
| "Trace all paths through X" | `get_lineage(X, direction="both")` | Includes all relation kinds |
| "What changes if X changes" | `get_impact(X)` | Returns backward transitive closure |
| "Does A depend on B" | `get_module_dependencies()` then check module graph | Module-level dependency view |
| "Find dead code" | `list_dead_code()` | Computed over all reachable edge kinds |

## Known Limitations (Built-in to Static Analysis)

1. **SPI & Service Registries**: META-INF/services/* and META-INF/dubbo/internal/* files register providers but are not Java source. Parser does not yet extract PROVIDES relations from these files (Issue #39 will fix this).

2. **Reflection-based Dispatch**: When code uses `getMethod()`, `newInstance()`, or similar reflection APIs, the target is invisible to static analysis. Check for `resolution="external_or_dynamic"` to detect.

3. **Config-driven Routes**: Spring `@Bean` factories, Dubbo configuration, or YAML routing rules may specify classes/methods not visible via code references.

4. **Method Body Internals**: If a method body is a single-liner or short call, the graph may not expand internal transitive chains. Use `get_lineage()` to traverse more deeply.

## Agent Decision Rules

When reasoning about code impact or completeness:

1. **Never assume completeness** if any relation has `resolution="external_or_dynamic"`.
   - Example: "The data model didn't change, so consumers don't change" fails if consumers have internal logic changes → Check via `get_lineage(X)` for callers.

2. **Prefer verification over speculation**.
   - When unsure whether a symbol is used, check reachability with `get_reachability(symbol_id)` before assuming it's dead.
   - When predicting file changes, prefer to name files in the graph over guessing new files.

3. **Use relation kind filtering** to avoid over-generalizing.
   - "Who calls X?" requires `kind="CALLS"`.
   - "Who implements interface X?" requires `kind="INHERITS"`.
   - Not filtering is the most common source of spurious connections.

4. **When analysis hits a limit** (`resolution="external_or_dynamic"`):
   - Do NOT conclude "I don't know"; instead, list alternative mechanisms
   - Ask the human to verify or check logs/configs
   - Mark high-risk for code review

## Ontology Version

This ontology describes **schema version 1.0** with relation kinds: CALLS, INHERITS, OVERRIDES, DECORATES.
PROVIDES extraction (Issue #39) will extend schema version 1.1.
Use `trigger_analysis()` to refresh the graph when new analyzers are available.
"""


def create_mcp_server() -> MCPServer:
    """Create and configure the MCP server."""
    server = MCPServer("lineagelens", instructions=_get_ontology_instructions())

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
        """Find all symbols that reference this one (all relation kinds).

        Returns relations of all kinds: CALLS (direct invocation), INHERITS (implementation
        of interface), DECORATES (annotation), OVERRIDES (method override), and PROVIDES
        (SPI registration). Filter by relation.kind to isolate specific patterns.

        IMPORTANT: SPI/service-provider dispatch from META-INF/services/* and
        META-INF/dubbo/internal/* is not yet extracted into PROVIDES relations (see
        Issue #39). When resolution=="external_or_dynamic", the symbol may have dynamic
        providers invisible to static analysis.

        Args:
            symbol_id: Symbol ID

        Returns:
            List of relations where this symbol is the target, grouped by kind
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            relations = query_get_callers(graph, symbol_id, get_cached_index(PROJECT_PATH))
            return {"symbol_id": symbol_id, "callers": [asdict(r) for r in relations]}
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def get_callees(symbol_id: str, **kwargs: Any) -> dict[str, Any]:
        """Find all symbols this one references (direct calls, base classes, etc.).

        Returns relations of multiple kinds: CALLS (direct invocation), INHERITS (extends/implements),
        OVERRIDES (overridden methods). Filter by relation.kind to isolate specific patterns
        (e.g., kind="CALLS" for actual function calls).

        Args:
            symbol_id: Symbol ID

        Returns:
            List of relations where this symbol is the source, grouped by kind
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

        Computes all symbols that depend on this one (transitively), over all relation kinds.
        Helps identify blast radius when modifying a symbol.

        IMPORTANT: This traversal uses static analysis only. Reflection-based consumers
        (resolution=="external_or_dynamic") are not included. Before deleting or breaking
        a symbol, also check for dynamic callers via `get_reachability()`.

        Args:
            symbol_id: Symbol ID
            max_depth: Maximum traversal depth

        Returns:
            Symbols affected by changes to this one, including entry points that would break
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
    async def list_implementations(interface_id: str, **kwargs: Any) -> dict[str, Any]:
        """Find all classes that implement a given interface.

        Filters results from get_callers() by relation.kind=="INHERITS" to show only
        direct implementers of the interface. Useful for understanding extension points
        and finding all implementations of a plugin interface or SPI.

        Args:
            interface_id: Symbol ID of the interface (e.g., 'java.io.Serializable')

        Returns:
            Dictionary with:
            - interface_id: The queried interface
            - count: Number of implementing classes
            - implementations: List of implementing class symbols with metadata
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            index = get_cached_index(PROJECT_PATH)

            # Get all symbols that reference this interface
            all_relations = query_get_callers(graph, interface_id, index)

            # Filter to INHERITS relations only
            implementations = []
            seen_ids = set()
            for rel in all_relations:
                if rel.kind == "INHERITS" and rel.source not in seen_ids:
                    seen_ids.add(rel.source)
                    sym = graph.symbols.get(rel.source)
                    if sym:
                        implementations.append(asdict(sym))

            return {
                "interface_id": interface_id,
                "count": len(implementations),
                "implementations": implementations,
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}

    @server.tool()
    async def list_providers(service_id: str, **kwargs: Any) -> dict[str, Any]:
        """Find all providers of an SPI or service interface.

        Filters results from get_callers() by relation.kind=="PROVIDES" to show only
        providers registered via service-provider interface (SPI) mechanisms.

        IMPORTANT: PROVIDES relations are extracted from service registry files
        (META-INF/services/*, META-INF/dubbo/internal/*, etc.). Until Issue #39
        is implemented, this tool will return empty results as PROVIDES relations
        are not yet available in the graph.

        Args:
            service_id: Symbol ID of the service interface
                       (e.g., 'org.apache.dubbo.rpc.Protocol')

        Returns:
            Dictionary with:
            - service_id: The queried service
            - count: Number of registered providers
            - providers: List of provider symbols with registry_source metadata
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)
            index = get_cached_index(PROJECT_PATH)

            # Get all symbols that provide this service
            all_relations = query_get_callers(graph, service_id, index)

            # Filter to PROVIDES relations only
            providers = []
            seen_ids = set()
            for rel in all_relations:
                if rel.kind == "PROVIDES" and rel.source not in seen_ids:
                    seen_ids.add(rel.source)
                    sym = graph.symbols.get(rel.source)
                    if sym:
                        provider_data = asdict(sym)
                        # Include the registry source if available in the relation arguments
                        if hasattr(rel, 'arguments') and rel.arguments:
                            provider_data["registry_source"] = rel.arguments.get("registry_file")
                        providers.append(provider_data)

            return {
                "service_id": service_id,
                "count": len(providers),
                "providers": providers,
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

    @server.tool()
    async def list_fields_by_type(
        type_name: str, include_subtypes: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        """Find all class fields of a given type across the codebase.

        Scans all class symbols and their fields to find matches for the specified type.
        Useful for finding all places where a particular type is used as a field.

        Args:
            type_name: Fully qualified type name (e.g., "java.lang.String", "models.User")
            include_subtypes: If true, also include fields of subtypes (requires MRO traversal)

        Returns:
            Dictionary with:
            - count: Number of matching fields found
            - fields: List of field metadata dicts, each containing:
              - field_id: Full identifier (class.fieldname)
              - field_name: Short field name
              - class_id: ID of containing class
              - class_name: Short class name
              - type: Field type
              - visibility: "public", "private", "protected", or "unknown"
              - static: Boolean
              - line: Line number in source file
              - file: File path relative to project root
              - description: Field docstring or None
        """
        try:
            graph = get_cached_graph(PROJECT_PATH)

            # Validate type_name format
            if not type_name or not _is_valid_type_name(type_name):
                return {"error": f"Invalid type name format: {type_name}"}

            results = []

            # Find all classes and check their fields
            for symbol in graph.symbols.values():
                if symbol.kind != "class":
                    continue

                # Check each field declared in the class
                for field_dict in symbol.fields:
                    field_type = field_dict.get("type", "unknown")

                    # Match by exact type or subtype
                    if _type_matches(graph, field_type, type_name, include_subtypes):
                        field_name = field_dict.get("name", "unknown")
                        field_id = f"{symbol.id}.{field_name}"

                        # Try to get the actual field symbol for additional metadata
                        field_symbol = graph.symbols.get(field_id)

                        visibility = "unknown"
                        is_static = False
                        if field_symbol:
                            visibility = field_symbol.visibility or "unknown"
                            is_static = field_symbol.static_

                        results.append({
                            "field_id": field_id,
                            "field_name": field_name,
                            "class_id": symbol.id,
                            "class_name": symbol.name,
                            "type": field_type,
                            "visibility": visibility,
                            "static": is_static,
                            "line": field_dict.get("line", 0),
                            "file": symbol.file,
                            "description": field_symbol.description if field_symbol else None,
                        })

            return {
                "count": len(results),
                "type_name": type_name,
                "include_subtypes": include_subtypes,
                "fields": results,
            }
        except GraphNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Error in list_fields_by_type: {e}", exc_info=True)
            return {"error": str(e)}

    return server


def main() -> None:
    """Entry point for the MCP server (lineagelens-mcp command)."""
    server = create_mcp_server()
    logger.info("Starting LineageLens MCP server on stdio")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
