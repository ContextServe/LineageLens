"""Tests for queries.py (single source of truth for graph traversal)."""

from lineagelens.model import CodeGraph, Container, Evidence, Relation, ResiliencySignal, Symbol
from lineagelens.queries import (
    get_callers,
    get_callees,
    get_lineage,
    get_module_overview,
    get_symbol,
    impact_analysis,
    list_entry_points,
    list_resiliency_risks,
    search_symbols,
)


def fixture_graph() -> CodeGraph:
    """Create a test graph with realistic call patterns."""
    graph = CodeGraph(project_root="/test")

    # Create containers (packages/modules)
    graph.add_container(Container(id="app", kind="package", name="app", file=None))
    graph.add_container(Container(id="app.api", kind="module", name="api", file="app/api.py", parent="app"))
    graph.add_container(Container(id="app.models", kind="module", name="models", file="app/models.py", parent="app"))

    # Create symbols
    # app.api.fetch_user (entry point, calls get_user)
    graph.add_symbol(
        Symbol(
            id="app.api.fetch_user",
            kind="function",
            name="fetch_user",
            file="app/api.py",
            line=10,
            end_line=15,
            module="app.api",
            parent="app.api",
            async_=True,
            description="Fetch user by ID",
            entry_point_kinds=["api_route"],
        )
    )

    # app.api.get_user (calls User.find)
    graph.add_symbol(
        Symbol(
            id="app.api.get_user",
            kind="function",
            name="get_user",
            file="app/api.py",
            line=20,
            end_line=25,
            module="app.api",
            parent="app.api",
            description="Get user from database",
        )
    )

    # app.models.User (class)
    graph.add_symbol(
        Symbol(
            id="app.models.User",
            kind="class",
            name="User",
            file="app/models.py",
            line=5,
            end_line=30,
            module="app.models",
            parent="app.models",
            description="User model",
        )
    )

    # app.models.User.find (method, calls execute_sql)
    graph.add_symbol(
        Symbol(
            id="app.models.User.find",
            kind="method",
            name="find",
            file="app/models.py",
            line=12,
            end_line=18,
            module="app.models",
            parent="app.models.User",
            description="Find user by ID",
        )
    )

    # app.models.execute_sql (helper)
    graph.add_symbol(
        Symbol(
            id="app.models.execute_sql",
            kind="function",
            name="execute_sql",
            file="app/models.py",
            line=25,
            end_line=30,
            module="app.models",
            parent="app.models",
            resiliency=[
                ResiliencySignal(
                    category="data_write",
                    severity="review",
                    evidence=Evidence(tier="deterministic_heuristic", label="data_write"),
                    line=27,
                )
            ],
        )
    )

    # Create relations: fetch_user → get_user → User.find → execute_sql
    evidence = Evidence(tier="deterministic_fact", label="static_ast")

    graph.add_relation(
        Relation(
            source="app.api.fetch_user",
            target="app.api.get_user",
            kind="AWAIT_CALLS",
            file="app/api.py",
            line=11,
            evidence=evidence,
        )
    )

    graph.add_relation(
        Relation(
            source="app.api.get_user",
            target="app.models.User.find",
            kind="CALLS",
            file="app/api.py",
            line=21,
            evidence=evidence,
        )
    )

    graph.add_relation(
        Relation(
            source="app.models.User.find",
            target="app.models.execute_sql",
            kind="CALLS",
            file="app/models.py",
            line=14,
            evidence=evidence,
        )
    )

    return graph


def test_get_symbol():
    graph = fixture_graph()
    sym = get_symbol(graph, "app.api.fetch_user")
    assert sym is not None
    assert sym.name == "fetch_user"
    assert sym.entry_point == "api_route"

    missing = get_symbol(graph, "nonexistent")
    assert missing is None


def test_search_symbols():
    graph = fixture_graph()

    # Search by name
    results = search_symbols(graph, "fetch")
    assert len(results) == 1
    assert results[0].name == "fetch_user"

    # Search by module
    results = search_symbols(graph, "app.models")
    assert len(results) >= 3  # User, find, execute_sql

    # Filter by kind
    results = search_symbols(graph, "user", kind="method")
    assert len(results) == 1  # "user" is in "User.find" id, and it's a method
    assert results[0].id == "app.models.User.find"

    results = search_symbols(graph, "user", kind="class")
    assert len(results) == 1
    assert results[0].kind == "class"

    # Filter by entry_point
    results = search_symbols(graph, "", entry_point="api_route")
    assert len(results) == 1
    assert results[0].entry_point == "api_route"


def test_get_callers_and_callees():
    graph = fixture_graph()

    # get_user is called by fetch_user
    callers = get_callers(graph, "app.api.get_user")
    assert len(callers) == 1
    assert callers[0].source == "app.api.fetch_user"

    # fetch_user calls get_user
    callees = get_callees(graph, "app.api.fetch_user")
    assert len(callees) == 1
    assert callees[0].target == "app.api.get_user"

    # User.find has no callers in this graph
    callers = get_callers(graph, "app.models.User.find")
    assert len(callers) == 1
    assert callers[0].source == "app.api.get_user"


def test_get_lineage():
    graph = fixture_graph()

    # Forward lineage from fetch_user: fetch_user → get_user → User.find → execute_sql
    lineage = get_lineage(graph, "app.api.fetch_user", direction="forward", max_depth=5)
    symbols_in_path = [step.symbol_id for step in lineage]
    assert "app.api.get_user" in symbols_in_path
    assert "app.models.User.find" in symbols_in_path
    assert "app.models.execute_sql" in symbols_in_path

    # Backward lineage from execute_sql: should reach fetch_user
    lineage = get_lineage(graph, "app.models.execute_sql", direction="backward", max_depth=5)
    symbols_in_path = [step.symbol_id for step in lineage]
    assert "app.models.User.find" in symbols_in_path
    assert "app.api.get_user" in symbols_in_path
    assert "app.api.fetch_user" in symbols_in_path


def test_impact_analysis():
    graph = fixture_graph()

    # What breaks if we change execute_sql? Everything upstream.
    impact = impact_analysis(graph, "app.models.execute_sql", max_depth=10)
    affected_ids = [step.symbol_id for step in impact.affected]

    assert "app.models.User.find" in affected_ids
    assert "app.api.get_user" in affected_ids
    assert "app.api.fetch_user" in affected_ids

    # fetch_user is an entry point
    assert any("fetch_user" in ep for ep in impact.affected_entry_points)


def test_get_module_overview():
    graph = fixture_graph()

    overview = get_module_overview(graph, "app.api")
    assert overview is not None
    assert overview.module_id == "app.api"
    assert len(overview.symbols) == 2  # fetch_user, get_user

    overview = get_module_overview(graph, "app.models")
    assert overview is not None
    assert len(overview.symbols) >= 2  # User, execute_sql (plus their methods)

    missing = get_module_overview(graph, "nonexistent.module")
    assert missing is None


def test_list_entry_points():
    graph = fixture_graph()

    entries = list_entry_points(graph)
    assert len(entries) == 1
    assert entries[0].id == "app.api.fetch_user"

    # Filter by kind
    routes = list_entry_points(graph, kind="api_route")
    assert len(routes) == 1
    assert routes[0].entry_point == "api_route"

    commands = list_entry_points(graph, kind="cli_command")
    assert len(commands) == 0


def test_list_resiliency_risks():
    graph = fixture_graph()

    risks = list_resiliency_risks(graph)
    assert len(risks) >= 1

    # execute_sql has a data_write risk
    sql_risks = [r for r in risks if r["symbol_id"] == "app.models.execute_sql"]
    assert len(sql_risks) == 1
    assert sql_risks[0]["category"] == "data_write"
    assert sql_risks[0]["severity"] == "review"

    # Filter by severity
    high_risks = list_resiliency_risks(graph, min_severity="high")
    # execute_sql is "review", not "high", so should be filtered out
    assert all(r["severity"] != "review" for r in high_risks if r["symbol_id"] == "app.models.execute_sql")
