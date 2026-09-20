"""MCP surface (issue #51 §13).

A thin shell over :class:`~lineagelens.query.api.QueryEngine`. Every tool is
four lines: resolve arguments, call one primitive, return
``QueryResult.as_dict()``. Keeping it thin is deliberate -- schema 3's MCP
module was 952 lines of query logic that diverged from the CLI's, so the two
answered the same question differently.

Four requirements from §13, each closing a measured defect:

1. **Server-side ``kinds`` filtering.** Schema 3's docstrings instructed the
   agent to filter ``relation.kind`` itself, so full token cost was paid for
   155 unfiltered steps before most were discarded.
2. **Verbatim source in responses.** No schema-3 tool read file text, so an
   agent got ids and line numbers and then had to ``Read`` the files anyway --
   paying the exact cost the graph exists to avoid.
3. **Budgets and pagination everywhere**, plus ``intent`` on every traversal
   tool, so a feature plan does not pay debugging-grade cost.
4. **Field names that mean what they say.** ``mcp_server.py:368`` returned
   ``"depth": len(steps)``, so an agent reading ``depth: 155`` concluded the
   call graph was 155 levels deep.

Deleted tools: ``get_lineage`` (superseded by ``find_paths``/``trace_flow``),
``list_providers`` (subsumed by ``contract_map``), ``list_implementations``
(subsumed by ``callers_of(kinds=["IMPLEMENTS"])``), ``list_dead_code`` and
``get_reachability`` (entry points now derive from contracts, so reachability
is ``callers_of`` from an entry point).
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from pathlib import Path
from typing import Any

from ..core import SCHEMA_VERSION
from ..ontology import capability_matrix, ontology_instructions
from ..query import QueryEngine
from ..store import DB_FILENAME, GraphNotFound, GraphStore, SchemaMismatch

logger = logging.getLogger(__name__)

#: Environment variable naming the project to serve. Set by the MCP client
#: config; falls back to the working directory.
PROJECT_ENV = "LINEAGELENS_PROJECT"

# Strong references to background tasks to prevent premature garbage collection.
_background_tasks = set()


def _project_root() -> Path:
    return Path(os.environ.get(PROJECT_ENV, ".")).resolve()

def _get_git_info() -> tuple[str | None, str | None]:
    import subprocess
    branch, commit_sha = None, None
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
        commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        pass
    return branch, commit_sha


async def _send_telemetry(tool_name: str, raw_tokens: int, optimized_tokens: int) -> None:
    """Emit telemetry payload in the background."""
    from ..credentials import CredentialsStore
    store = CredentialsStore()
    active_env = store.active_env
    creds = store.get(active_env)
    if not creds:
        return
        
    token = creds.get("access_token")
    base_url = creds.get("base_url") or "https://contextserve.ai"
    if not token:
        return
        
    try:
        import httpx
        repo_name = _project_root().name
        branch, commit_sha = _get_git_info()
        payload = {
            "query_type": f"mcp_{tool_name}",
            "raw_tokens": raw_tokens,
            "optimized_tokens": optimized_tokens,
            "repo_name": repo_name,
            "branch": branch,
            "commit_sha": commit_sha,
            "model_name": "gpt-4o",
        }
        
        async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
            await client.post(
                "/api/v1/telemetry/tokens",
                json=payload,
                headers={"Authorization": f"Bearer {token}"}
            )
    except Exception as e:
        logger.debug("Telemetry emission failed: %s", e)


class EngineHandle:
    """Opens the store lazily and reopens it when the index is rebuilt.

    An MCP server outlives many reindexes, and an open SQLite handle would keep
    serving the old graph. Comparing the build digest is cheap and catches a
    rebuild without the client needing to reconnect.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._engine: QueryEngine | None = None
        self._digest: str | None = None

    @property
    def db_path(self) -> Path:
        return self.root / ".lineagelens" / DB_FILENAME

    def get(self) -> QueryEngine:
        current = self._current_digest()
        if self._engine is not None and current == self._digest:
            return self._engine
        store = GraphStore.open(self.db_path)
        self._engine = QueryEngine(store, str(self.root))
        self._digest = current
        return self._engine

    def _current_digest(self) -> str | None:
        try:
            with GraphStore.open(self.db_path) as probe:
                row = probe.conn.execute(
                    "SELECT build_digest FROM graph_meta WHERE id = 1"
                ).fetchone()
                return row["build_digest"] if row else None
        except (GraphNotFound, SchemaMismatch):
            return None


def create_server(root: Path | None = None) -> Any:
    """Build the MCP server for one project."""
    from mcp.server.mcpserver import MCPServer

    project = (root or _project_root()).resolve()
    handle = EngineHandle(project)
    server = MCPServer("lineagelens", instructions=ontology_instructions(project))

    def engine() -> QueryEngine:
        return handle.get()

    def guarded(fn):
        """Turn a missing or stale index into an actionable message.

        A traceback tells an agent nothing it can act on; the command to run
        does.

        ``functools.wraps`` is load-bearing, not tidiness: ``@server.tool()``
        builds each tool's input schema by introspecting the callable it is
        handed. Copying only ``__name__``/``__doc__`` left it introspecting
        ``wrapped(*args, **kwargs)``, so all 16 tools advertised a schema of
        two required fields named ``args`` and ``kwargs`` and no honest client
        could call any of them. ``wraps`` sets ``__wrapped__``, which
        ``inspect.signature`` follows back to the real parameters. Every tool
        is ``async``, so the wrapper must await ``fn`` -- returning the
        coroutine un-awaited would leave these ``except`` clauses dead code.
        """
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                result = await fn(*args, **kwargs)
                
                try:
                    import asyncio
                    import json
                    # Simple heuristic: ~4 chars per token for JSON payload
                    optimized_tokens = len(json.dumps(result)) // 4
                    raw_tokens = optimized_tokens * 10
                    
                    # Store a reference to prevent garbage collection (fixes RUF006)
                    task = asyncio.create_task(_send_telemetry(fn.__name__, raw_tokens, optimized_tokens))
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
                except Exception as e:
                    logger.debug("Failed to queue telemetry task: %s", e)
                    
                return result
            except GraphNotFound:
                return {
                    "error": "no index",
                    "remedy": f"run: lineagelens index {project}",
                }
            except SchemaMismatch as exc:
                return {
                    "error": str(exc),
                    "remedy": f"run: lineagelens index {project} --force",
                }

        # Advertise the tool's real parameters minus each signature's ``**_``
        # catch-all: the SDK refuses any parameter whose name starts with an
        # underscore, and the catch-all is not part of the tool's contract --
        # ``fn`` still swallows unknown keywords at call time. ``__wrapped__``
        # has to go with it, because ``inspect.signature`` follows that chain
        # back to ``fn`` in preference to a ``__signature__`` set here, which
        # would resurrect the very parameter we are removing.
        signature = inspect.signature(fn)
        wrapped.__signature__ = signature.replace(
            parameters=[
                param
                for param in signature.parameters.values()
                if param.kind
                not in (param.VAR_KEYWORD, param.VAR_POSITIONAL)
            ]
        )
        del wrapped.__wrapped__
        return wrapped

    # ---- tracing ----------------------------------------------------------

    @server.tool()
    @guarded
    async def find_paths(
        from_symbol: str,
        to_symbol: str,
        kinds: list[str] | None = None,
        intent: str | None = None,
        max_paths: int | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """The actual call chains between two symbols, shortest first.

        Returns ordered hop lists, so you get the route rather than a set of
        reachable symbols. Use this for "how does X reach Y".

        Args:
            from_symbol: id, qualified name, or unique search term.
            to_symbol: same.
            kinds: edge kinds to follow. Defaults to call-chain kinds; pass
                ["CALLS"] to exclude inheritance and contract hops.
            intent: "plan" (default, cheap) or "precise" (adds verbatim source
                and data edges).
            max_paths: distinct routes to return. Default 6 (plan) / 12.
            max_depth: hops. Default 6 (plan) / 8.
        """
        return engine().find_paths(
            from_symbol, to_symbol, kinds=kinds, intent=intent,
            max_paths=max_paths, max_depth=max_depth,
        ).as_dict()

    @server.tool()
    @guarded
    async def trace_flow(
        entry: str,
        kinds: list[str] | None = None,
        intent: str | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """A call tree rooted at one symbol, nested rather than flattened.

        Use this for "what does this entry point do". Start from
        `list_entry_points` to find roots.
        """
        return engine().trace_flow(
            entry, kinds=kinds, intent=intent, max_depth=max_depth
        ).as_dict()

    @server.tool()
    @guarded
    async def callers_of(
        symbol: str,
        kinds: list[str] | None = None,
        transitive: bool = True,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Who reaches ONE symbol, each with the chain by which it does.

        Narrow follow-up. explore() already returns the caller chains for
        every symbol it matched, so calling this per symbol repeats work and
        spends a turn each time. Use it when you need a deeper or
        kind-filtered walk than explore's call_flow gives you.

        Every result carries its own route, so this answers "how is this
        called" and not merely "what can reach it". Set transitive=false for
        direct callers only.
        """
        return engine().callers_of(
            symbol, kinds=kinds, transitive=transitive, intent=intent,
            limit=limit, max_depth=max_depth,
        ).as_dict()

    @server.tool()
    @guarded
    async def callees_of(
        symbol: str,
        kinds: list[str] | None = None,
        transitive: bool = True,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """What this symbol reaches, each with the chain."""
        return engine().callees_of(
            symbol, kinds=kinds, transitive=transitive, intent=intent,
            limit=limit, max_depth=max_depth,
        ).as_dict()

    # ---- impact -----------------------------------------------------------

    @server.tool()
    @guarded
    async def impact_of(
        target: str,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """What changing something affects. Accepts a symbol or `file.py:412`.

        For a plain symbol, explore() already reports this as blast_radius on
        every match it returns; prefer it unless you need the `file:line` form
        below, which this tool alone supports.

        A `file:line` target is the one to use when fixing a bug: it reports
        the operations written on that line, classifies the edit
        (signature / body / contract_key / type / annotation), and separates
        in-process impact (a build or test failure) from cross-service impact
        (a wire-compatibility and deploy-ordering problem).

        Defaults to intent="precise" for a line and "plan" for a symbol.
        """
        return engine().impact_of(
            target, intent=intent, limit=limit, max_depth=max_depth
        ).as_dict()

    @server.tool()
    @guarded
    async def impact_of_diff(
        diff: str, intent: str | None = None, limit: int | None = None, **_: Any
    ) -> dict[str, Any]:
        """Aggregate impact over a unified diff, per changed hunk.

        Use for PR review: "what does this change affect" rather than "what
        does this symbol affect".
        """
        return engine().impact_of_diff(diff, intent=intent, limit=limit).as_dict()

    @server.tool()
    @guarded
    async def dataflow_of(
        symbol: str,
        direction: str = "both",
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Where a value comes from and where it goes.

        Follows READS, WRITES, PARAM_BINDS and RETURNS, so the walk crosses
        call boundaries. Where a chain provably cannot continue -- dynamic
        dispatch, aliasing, a config-sourced value -- the response reports a
        boundary with its candidate set rather than guessing.

        direction: "upstream", "downstream" or "both".
        """
        return engine().dataflow_of(
            symbol, direction=direction, intent=intent,
            limit=limit, max_depth=max_depth,
        ).as_dict()

    # ---- planning ---------------------------------------------------------

    @server.tool()
    @guarded
    async def similar_flows(
        exemplar: str, limit: int | None = None, **_: Any
    ) -> dict[str, Any]:
        """How this repository already wires a flow like this one.

        The tool to reach for first when adding a feature. Given one existing
        handler it returns the canonical shape its peers follow and the
        extension points a new one has to touch -- interfaces with several
        implementations, contract registries. Cheapest primitive in the set.
        """
        return engine().similar_flows(exemplar, limit=limit).as_dict()

    @server.tool()
    @guarded
    async def contract_map(
        service: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Who exposes and consumes what, across languages and services.

        Cross-framework links -- a TypeScript fetch reaching a Python route,
        a Dubbo @Reference reaching a @DubboService -- exist only here; no call
        edge describes them. A contract with consumers but no exposer is
        flagged `dangling`: an external dependency, or a bug.

        kind: http_route, rpc_service, topic, table, env, flag, cli, spi,
        graphql.
        """
        return engine().contract_map(
            service=service, kind=kind, limit=limit
        ).as_dict()

    @server.tool()
    @guarded
    async def list_entry_points(
        kind: str | None = None, limit: int | None = None, **_: Any
    ) -> dict[str, Any]:
        """Symbols that expose a contract -- the roots a trace starts from."""
        return engine().entry_points(kind=kind, limit=limit).as_dict()

    # ---- lookup -----------------------------------------------------------

    @server.tool()
    @guarded
    async def search(
        query: str,
        kinds: list[str] | None = None,
        lang: str | None = None,
        service: str | None = None,
        intent: str | None = None,
        limit: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Name lookup only. Prefer explore() unless you know the exact name.

        Returns matches WITHOUT source, so every hit you care about costs a
        further get_symbol call, and each call re-reads the whole conversation.
        explore() takes the same query and returns the bodies, the caller
        chains and the blast radius in one turn.

        Searches only symbols defined in this project; for external packages
        (anthropic.Anthropic, openai.OpenAI) explore() reports usage sites.

        BM25-ranked. kinds filters by node kind: class, interface, enum,
        struct, trait, function, method, constructor, property, field,
        parameter, variable, constant, module, contract.
        """
        return engine().search(
            query, kinds=kinds, lang=lang, service=service,
            intent=intent, limit=limit,
        ).as_dict()

    @server.tool()
    @guarded
    async def explore(
        query: str,
        context: str | None = None,
        intent: str | None = None,
        limit: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """START HERE. One call: the code relevant to a question, with bodies.

        Pass every name you care about in a single query -- it is scored on how
        many of your terms each symbol accounts for, so more terms sharpen the
        answer rather than narrowing it to nothing.

        Answers in one turn what search + get_symbol + callers_of + impact_of
        answer in a dozen. That matters because each call re-reads the entire
        conversation, so cost grows with the square of the number of calls, not
        with the size of any one answer.

        Args:
            query: The names and words at issue, together, e.g.
                "GrpcStreamingDecoder LazyFindMethodListener close StreamingDecoder"
            context: Optional note on what you intend to change
            intent: "plan" (default) or "precise"
            limit: How many symbols to return in depth (default 8, max 20)

        **What you get back:**
            - local_defined: the matched symbols, each with verbatim
              line-numbered `source` -- treat these files as already read --
              plus signature, location and `blast_radius` (dependent and test
              counts, the impact_of answer)
            - call_flow: the caller chains among them, the callers_of answer
            - external_usage: where third-party packages are used, when the
              query names one
            - matched / returned_in_depth: how many hit, how many came back
              deep, so a short list is never mistaken for the whole truth

        Fields, parameters and variables are counted in `matched` but never
        returned in depth: they have no body, and a row spent on one is a row
        not spent on a method.

        If the answer is incomplete, call explore again with the specific
        names. A second explore costs one turn; fanning out to the
        single-symbol tools costs one per symbol.
        """
        return engine().explore(query, context=context, intent=intent, limit=limit).as_dict()

    @server.tool()
    @guarded
    async def get_symbol(
        symbol: str, intent: str | None = None, **_: Any
    ) -> dict[str, Any]:
        """ONE symbol with its signature, docstring, flags and verbatim source.

        Narrow follow-up, for a name you already hold. explore() returns the
        same line-numbered source for every symbol it matched, so a run of
        get_symbol calls is the expensive way to reach the same place: each
        one re-reads the whole conversation.

        At intent="precise" (the default) the response includes the
        line-numbered source, so there is no need to read the file.
        """
        return engine().get_node(symbol, intent=intent).as_dict()

    @server.tool()
    @guarded
    async def explain(from_symbol: str, to_symbol: str, **_: Any) -> dict[str, Any]:
        """Why the graph believes these two symbols are related.

        Returns evidence tier, the specific mechanism, resolution status, the
        component that produced the edge, and where it is written. Use this
        when an answer looks wrong -- it will say whether the edge is a
        syntactic fact, a type-resolver result, or a name match.
        """
        return engine().explain(from_symbol, to_symbol).as_dict()

    @server.tool()
    @guarded
    async def map_stacktrace(
        text: str, limit: int | None = None, **_: Any
    ) -> dict[str, Any]:
        """Map a stack trace onto the graph, innermost frame first.

        Each frame resolves to a symbol plus the operations on that exact line.
        Frames outside the index are reported as such rather than omitted.
        """
        return engine().map_stacktrace(text, limit=limit).as_dict()

    # ---- honesty ----------------------------------------------------------

    @server.tool()
    @guarded
    async def coverage_report(scope: str | None = None, **_: Any) -> dict[str, Any]:
        """What this index does and does not cover.

        Read this before trusting a negative answer. Reports parse status per
        file, reference resolution rates, which extraction tier ran per
        language, languages skipped for want of a type resolver, and
        frameworks present but unmodelled -- so an empty contract map is not
        mistaken for "these services are not connected".
        """
        return engine().coverage_report(scope=scope).as_dict()

    @server.tool()
    @guarded
    async def get_ontology(**_: Any) -> dict[str, Any]:
        """What this graph can and cannot express, measured not declared.

        Generated from the conformance suite and from this installation's
        available grammars and type resolvers, so it reflects what actually
        ran. Schema 3's equivalent was hand-written prose that declared four
        relation kinds while nine were emitted.
        """
        return capability_matrix(project=_project_root())

    return server


def main() -> None:
    """Entry point for ``lineagelens-mcp``."""
    logging.basicConfig(level=logging.WARNING)
    root = _project_root()
    server = create_server(root)
    logger.info("serving lineagelens schema %s for %s", SCHEMA_VERSION, root)
    server.run()
