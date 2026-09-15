"""Tests for the MCP surface (issue #51 §13).

These exist because the surface had no tests at all, and that gap let a real
defect ship: ``guarded`` copied only ``__name__``/``__doc__`` onto its
wrapper, so ``@server.tool()`` introspected ``wrapped(*args, **kwargs)`` and
every one of the 16 tools advertised a schema of two required fields named
``args`` and ``kwargs``. No honest client could call any tool -- every call
failed identically with a pydantic "field required" error, which reads to an
agent like an unreachable server rather than a broken schema. The whole test
suite passed throughout.

So the assertions here are deliberately about the *advertised contract*
rather than query results: the schema a client reads is the thing that was
wrong, and asserting on tool bodies would not have caught it.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from lineagelens.mcp.server import create_server

EXPECTED_TOOLS = {
    "find_paths",
    "trace_flow",
    "callers_of",
    "callees_of",
    "impact_of",
    "impact_of_diff",
    "dataflow_of",
    "similar_flows",
    "contract_map",
    "list_entry_points",
    "search",
    "explore",
    "get_symbol",
    "explain",
    "map_stacktrace",
    "coverage_report",
    "get_ontology",
}


@pytest.fixture(scope="module")
def tools(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Every tool, keyed by name.

    ``create_server`` resolves the engine lazily, so an unindexed directory is
    enough to build the server and read its schemas.
    """
    root = tmp_path_factory.mktemp("unindexed")
    server = create_server(Path(root))
    listed = server._tool_manager.list_tools()
    return {tool.name: tool for tool in listed}


def test_every_tool_is_registered(tools: dict[str, object]) -> None:
    assert set(tools) == EXPECTED_TOOLS


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_tool_advertises_its_real_parameters(
    name: str, tools: dict[str, object]
) -> None:
    """The regression: no tool may advertise ``*args``/``**kwargs``.

    A wrapper that loses its wrappee's signature shows up here as parameters
    literally named ``args`` and ``kwargs``.
    """
    schema = tools[name].parameters
    properties = schema.get("properties", {})

    assert "args" not in properties, (
        f"{name} advertises a positional catch-all: its decorator lost the "
        f"wrapped function's signature"
    )
    assert "kwargs" not in properties, (
        f"{name} advertises a keyword catch-all: its decorator lost the "
        f"wrapped function's signature"
    )


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_tool_hides_its_underscore_catch_all(
    name: str, tools: dict[str, object]
) -> None:
    """``**_`` must not reach the schema.

    The SDK raises ``InvalidSignature`` for any parameter starting with an
    underscore, so a leaked catch-all takes the whole server down at startup
    rather than failing one tool.
    """
    properties = tools[name].parameters.get("properties", {})
    leaked = [key for key in properties if key.startswith("_")]
    assert not leaked, f"{name} leaks private parameters: {leaked}"


def test_required_parameters_match_the_signatures(
    tools: dict[str, object],
) -> None:
    """Arguments without defaults are required; the rest are optional.

    This pins the schema to the function signature in both directions, so a
    future wrapper cannot quietly mark everything optional (or required) while
    still passing the catch-all checks above.
    """
    for name, tool in tools.items():
        signature = inspect.signature(tool.fn)
        expected = {
            parameter.name
            for parameter in signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind
            not in (
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            )
        }
        actual = set(tool.parameters.get("required", []))
        assert actual == expected, f"{name}: required {actual} != {expected}"


@pytest.mark.anyio
async def test_missing_index_returns_a_remedy_not_a_traceback(
    tools: dict[str, object],
) -> None:
    """``guarded`` has to await its wrappee to catch anything.

    Every tool is ``async``, so a synchronous wrapper would return the
    coroutine un-awaited and its ``except GraphNotFound`` clause would be dead
    code -- the traceback would surface to the agent instead of the one thing
    it can act on.

    ``search`` rather than ``get_ontology``: the latter answers from the
    static capability matrix and so succeeds without an index at all.
    """
    result = await tools["search"].fn(query="anything")

    assert result["error"] == "no index"
    assert result["remedy"].startswith("run: lineagelens index ")
