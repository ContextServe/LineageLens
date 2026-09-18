"""HTTP surface over the schema-4 graph (#55).

Consumed by ``frontend/`` and by anything that would rather speak HTTP than
MCP. It is a thin projection of :class:`~lineagelens.query.QueryEngine` and
holds no query logic of its own -- the previous version reimplemented traversal
against an in-memory graph, which is how it came to disagree with the CLI.

Two properties are deliberate and worth preserving:

**Every answer carries its coverage envelope.** The envelope is what makes a
negative answer trustworthy, and a transport is exactly the place it tends to
get dropped for being inconvenient to type. So query routes return the
engine's own ``as_dict()`` rather than a hand-written response model.

**A capability that does not exist returns 501, not an empty list.** Dead-code
and reachability verdicts have no schema-4 producer yet, and resiliency signals
land in #60. Answering ``[]`` would be indistinguishable from "analysed, found
nothing" -- the single failure mode this codebase spends the most effort
avoiding.

**Use ``node_id`` in URLs, not a qualified name.** Every schema-4 qualified
name contains ``/`` and ``#`` -- ``shop/python/repo#Repository/save``. An
unencoded ``#`` is a URL *fragment*, so a client that forgets to encode sends
only ``shop/python/repo``; the server receives a valid request for the *module*
and answers it correctly. Nothing arrives that looks wrong, so no amount of
validation can catch it.

The fix is to stop requiring the fragile form. Node ids are content hashes and
contain no URL-special characters, so every query result carries ``node_id``
alongside ``node`` and any answer can be turned into the next URL with no
encoding at all (#67). Qualified names still work, percent-encoded, because
they are what a human reads.

Two safety nets for the cases that remain: a request whose id resolves to a
container although the caller did not ask for one carries a ``note`` saying the
id may have been truncated, and an id containing raw control characters is
rejected outright, since no correct client produces one.

FastAPI is an optional dependency (``pip install lineagelens[rest]``). Import
this module only when serving.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from .core import NodeFlags
from .query import QueryEngine
from .store import GraphNotFound, SchemaMismatch

#: Ceiling on nodes returned by ``/graph/view``. Cytoscape's compound layout is
#: the binding constraint long before SQLite is.
GRAPH_VIEW_LIMIT = 2000

#: Sub-resources hanging off ``/symbols/{id}``. Registered *before* the bare
#: ``/symbols/{id:path}`` route, which matches greedily and would otherwise
#: swallow them as part of the id. Ordering is the whole mechanism, so the
#: tuple exists to be asserted against in ``tests/test_rest.py``.
SYMBOL_SUBRESOURCES: tuple[str, ...] = (
    "callers", "callees", "lineage", "impact", "dataflow",
)

#: Fields the visualiser renders that have no schema-4 producer yet. Named in
#: the response so the dashboard can grey out a filter instead of showing it
#: switched off over data that was never computed.
UNSUPPORTED_NODE_FIELDS: tuple[str, ...] = (
    "verdict",              # reachability -- gated on #49
    "rescue_mechanism",
    "rescue_tier",
    "has_resiliency_flag",  # #60
)


# ---------------------------------------------------------------------------
# visualisation models
#
# Only the graph view is typed. The frontend depends on its field names, and a
# breaking change there should fail here rather than in a browser.
# ---------------------------------------------------------------------------

class NodeView(BaseModel):
    """One node, as the visualiser needs it -- not the full detail."""

    id: str
    #: The name every *query* route reports hops and results under. Node ids
    #: are content hashes and are what the graph is keyed by, so a client that
    #: wants to highlight the result of a traversal needs both: this is the
    #: join key between a picture and an answer.
    qualified_name: str
    label: str
    kind: str
    parent: str | None = None
    lang: str = ""
    service: str | None = None
    file: str = ""
    line: int = 0
    entry_point: bool = False
    async_: bool = False
    is_test: bool = False
    exported: bool = False
    deprecated: bool = False
    lines_of_code: int = 1
    duplicate_name: bool = False
    #: ``None`` means "not computed", never "false". See UNSUPPORTED_NODE_FIELDS.
    verdict: str | None = None
    rescue_mechanism: str | None = None
    rescue_tier: str | None = None
    has_resiliency_flag: bool | None = None
    scope: str = "source"


class EdgeView(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    resolution: str
    evidence_tier: str
    evidence_label: str = ""


class GraphView(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
    returned: int
    total_available: int
    truncated: bool
    coverage: dict[str, Any]
    unsupported: list[str]


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

def _not_implemented(capability: str, issue: str) -> HTTPException:
    """501 with the reason and where to follow it.

    Distinct from 404: the route exists and is intended, the producer does not.
    A client can tell "ask again after the next release" from "you have the URL
    wrong", which an empty 200 would not let it do at all.
    """
    return HTTPException(
        status_code=501,
        detail={
            "capability": capability,
            "reason": f"no schema-4 producer yet; tracked in {issue}",
            "hint": "the route is intentional and will answer once that lands",
        },
    )


#: Node kinds that contain other nodes. A request that resolved to one of
#: these, from an id with no ``#``, is the shape a fragment-truncated id takes.
_CONTAINER_KINDS: frozenset[str] = frozenset({
    "module", "package", "namespace", "file", "class", "interface",
})

#: Keys in a query result whose value is a symbol name a client may want to
#: turn back into a URL. Annotated with a sibling ``node_id``.
_NAME_KEYS: tuple[str, ...] = ("node", "target")

#: Characters that cannot appear in a correctly-encoded path segment. A raw
#: newline or NUL in an id means the client built the URL by string
#: concatenation without escaping, so the id is not trustworthy -- better a 400
#: than a fuzzy match through the engine's search fallback.
_ILLEGAL = frozenset(chr(c) for c in (*range(0x20), 0x7F))


def _reject_control_characters(symbol_id: str) -> None:
    bad = sorted(_ILLEGAL & set(symbol_id))
    if bad:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "symbol id contains raw control characters",
                "codepoints": [hex(ord(c)) for c in bad],
                "hint": "percent-encode the id, or use the node_id form",
            },
        )


def _annotate_ids(payload: Any, resolve: Any) -> Any:
    """Add ``node_id`` beside every symbol name in a result.

    Walks the whole payload rather than special-casing each primitive, because
    the shapes genuinely differ -- ``get_node`` returns one flat object,
    ``callers_of`` returns paths with a ``hops`` list, ``impact_of`` nests
    dependents under three separate keys -- and a per-primitive projection
    would be the place the next shape gets forgotten.

    Only annotates names that resolve. An unresolvable name is left alone
    rather than given a null id: a client checking ``if node_id`` should see
    absence, not a falsy value it has to special-case.
    """
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            out[key] = _annotate_ids(value, resolve)
            if key in _NAME_KEYS and isinstance(value, str):
                node_id = resolve(value)
                if node_id is not None:
                    out[f"{key}_id"] = node_id
        return out
    if isinstance(payload, list):
        return [_annotate_ids(item, resolve) for item in payload]
    return payload


def create_router(project: Path, api_key: str | None = None) -> APIRouter:
    """Build the ``/api/v1`` router for one indexed project.

    Args:
        project: project root, i.e. the directory holding ``.lineagelens/``.
        api_key: when set, every route requires a matching ``X-API-Key``.
            Unset means no auth, which is correct for ``localhost`` and wrong
            for anything else.
    """
    router = APIRouter(prefix="/api/v1", tags=["lineagelens"])
    root = Path(project)

    def verify_api_key(x_api_key: str | None = Header(None)) -> None:
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    def engine() -> Iterator[QueryEngine]:
        """One engine per request.

        The store's connection is not shared across threads and FastAPI runs
        sync handlers in a threadpool, so a module-level engine would be a
        latent ``ProgrammingError`` under concurrency. Opening SQLite is cheap;
        debugging that is not.
        """
        try:
            eng = QueryEngine.open(root)
        except GraphNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SchemaMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            yield eng
        finally:
            eng.store.close()

    guarded = [Depends(verify_api_key)]
    dep = Depends(engine)

    def _result(payload: Any, eng: QueryEngine,
                requested: str | None = None) -> dict[str, Any]:
        """Serialise a QueryResult, add ``node_id``s, and 404 a miss.

        The engine reports an unknown symbol in ``extra['error']`` rather than
        raising, because an MCP client wants the envelope alongside the miss.
        Over HTTP the status code is the idiom, so translate it.
        """
        out = payload.as_dict()
        if "error" in out:
            raise HTTPException(status_code=404, detail=out["error"])

        cache: dict[str, str | None] = {}

        def resolve(name: str) -> str | None:
            # One lookup per distinct name per request. A hub answer repeats
            # the same qualified name across dozens of hops.
            if name not in cache:
                node = eng.store.get_node(name)
                if node is None:
                    matches = eng.store.nodes_by_qualified_name(name)
                    node = matches[0] if len(matches) == 1 else None
                cache[name] = node.id if node else None
            return cache[name]

        out = _annotate_ids(out, resolve)
        note = _truncation_note(eng, requested, out)
        if note:
            out["note"] = note
        return out

    def _truncation_note(
        eng: QueryEngine, requested: str | None, out: dict[str, Any]
    ) -> str | None:
        """Warn when an id may have lost its ``#`` to a URL fragment.

        Cannot be an error. A caller may legitimately ask about a module, and
        the request that arrives is indistinguishable from one that meant a
        member -- that is the whole difficulty. So it is a hint on an otherwise
        correct answer, which costs nothing, rather than silence on a possibly
        wrong one, which is what #67 is about.

        Only fires when the requested id has no ``#`` and resolved to a
        container, so a genuine module query is not annotated on every call.
        """
        if not requested or "#" in requested:
            return None
        node = eng.store.get_node(requested)
        if node is None:
            matches = eng.store.nodes_by_qualified_name(requested)
            node = matches[0] if len(matches) == 1 else None
        if node is None or node.kind.value not in _CONTAINER_KINDS:
            return None
        return (
            f"resolved to a {node.kind.value}; the requested id has no '#'. "
            f"If you meant a member, percent-encode the id "
            f"(encodeURIComponent) or use the node_id form -- an unencoded '#' "
            f"is a URL fragment and never reaches the server."
        )

    # ---- visualisation ----------------------------------------------------

    @router.get("/graph/view", response_model=GraphView, dependencies=guarded)
    def graph_view(
        module: str | None = None,
        lang: str | None = None,
        service: str | None = None,
        limit: int = Query(GRAPH_VIEW_LIMIT, ge=1, le=GRAPH_VIEW_LIMIT),
        eng: QueryEngine = dep,
    ) -> GraphView:
        """The graph as a picture, bounded and honest about the bound."""
        nodes, edges, total = eng.store.graph_slice(
            module=module, lang=lang, service=service, limit=limit
        )

        seen: dict[tuple[str, str], int] = {}
        for node in nodes:
            key = (node.kind.value, node.name)
            seen[key] = seen.get(key, 0) + 1
        rendered = {n.id for n in nodes}

        node_views = [
            NodeView(
                id=n.id,
                qualified_name=n.qualified_name,
                label=n.name,
                kind=n.kind.value,
                # Null a parent outside the slice: Cytoscape needs the referent.
                parent=n.parent_id if n.parent_id in rendered else None,
                lang=n.lang,
                service=n.service_id,
                file=n.file_path,
                line=n.span.start_line,
                entry_point=n.has(NodeFlags.ENTRY_POINT),
                async_=n.has(NodeFlags.ASYNC),
                is_test=n.has(NodeFlags.TEST),
                exported=n.has(NodeFlags.EXPORTED),
                deprecated=n.has(NodeFlags.DEPRECATED),
                lines_of_code=max(1, n.span.end_line - n.span.start_line + 1),
                duplicate_name=seen[(n.kind.value, n.name)] > 1,
                scope="test" if n.has(NodeFlags.TEST) else "source",
            )
            for n in nodes
        ]
        edge_views = [
            EdgeView(
                id=f"{e.src}->{e.dst}:{e.kind.value}:{i}",
                source=e.src,
                target=e.dst,
                kind=e.kind.value,
                resolution=e.resolution.value,
                evidence_tier=e.evidence.tier.value,
                evidence_label=e.evidence.label or "",
            )
            for i, e in enumerate(edges)
        ]
        return GraphView(
            nodes=node_views,
            edges=edge_views,
            returned=len(node_views),
            total_available=total,
            truncated=total > len(node_views),
            coverage=eng.coverage_report().envelope.as_dict(),
            unsupported=list(UNSUPPORTED_NODE_FIELDS),
        )

    # ---- symbols ----------------------------------------------------------

    @router.get("/search", dependencies=guarded)
    def search(
        text: str,
        kind: str | None = None,
        lang: str | None = None,
        service: str | None = None,
        intent: str | None = None,
        limit: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        kinds = [k.strip() for k in kind.split(",")] if kind else None
        return _result(
            eng.search(
                text, kinds=kinds, lang=lang, service=service,
                intent=intent, limit=limit,
            ),
            eng,
        )

    @router.get("/symbols/{symbol_id:path}/callers", dependencies=guarded)
    def callers(
        symbol_id: str,
        transitive: bool = True,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        _reject_control_characters(symbol_id)
        return _result(
            eng.callers_of(symbol_id, transitive=transitive, intent=intent,
                           limit=limit, max_depth=max_depth),
            eng, symbol_id,
        )

    @router.get("/symbols/{symbol_id:path}/callees", dependencies=guarded)
    def callees(
        symbol_id: str,
        transitive: bool = True,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        _reject_control_characters(symbol_id)
        return _result(
            eng.callees_of(symbol_id, transitive=transitive, intent=intent,
                           limit=limit, max_depth=max_depth),
            eng, symbol_id,
        )

    @router.get("/symbols/{symbol_id:path}/lineage", dependencies=guarded)
    def lineage(
        symbol_id: str,
        direction: str = Query("forward", pattern="^(forward|backward)$"),
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        """Transitive callers or callees, kept for the frontend's URL shape.

        Schema 3's ``get_lineage`` returned a reachability *set* with no
        predecessor links, so its depths were DFS artefacts and no chain could
        be reconstructed from the response. This answers the question the
        frontend was actually asking, with real distances.
        """
        _reject_control_characters(symbol_id)
        walk = eng.callees_of if direction == "forward" else eng.callers_of
        out = _result(
            walk(symbol_id, transitive=True, intent=intent,
                 limit=limit, max_depth=max_depth),
            eng, symbol_id,
        )
        out["direction"] = direction
        return out

    @router.get("/symbols/{symbol_id:path}/impact", dependencies=guarded)
    def impact(
        symbol_id: str,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        _reject_control_characters(symbol_id)
        return _result(
            eng.impact_of(symbol_id, intent=intent, limit=limit,
                          max_depth=max_depth),
            eng, symbol_id,
        )

    @router.get("/symbols/{symbol_id:path}/dataflow", dependencies=guarded)
    def dataflow(
        symbol_id: str,
        direction: str = Query("both", pattern="^(both|forward|backward)$"),
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        _reject_control_characters(symbol_id)
        return _result(
            eng.dataflow_of(symbol_id, direction=direction, intent=intent,
                            limit=limit, max_depth=max_depth),
            eng, symbol_id,
        )

    # Registered last: ``{symbol_id:path}`` would otherwise swallow the
    # suffixed routes above, since a path converter matches greedily.
    @router.get("/symbols/{symbol_id:path}", dependencies=guarded)
    def symbol(
        symbol_id: str, intent: str | None = None, eng: QueryEngine = dep
    ) -> dict[str, Any]:
        _reject_control_characters(symbol_id)
        return _result(eng.get_node(symbol_id, intent=intent), eng, symbol_id)

    # ---- whole-graph questions -------------------------------------------

    @router.get("/entry-points", dependencies=guarded)
    def entry_points(
        kind: str | None = None, limit: int | None = None, eng: QueryEngine = dep
    ) -> dict[str, Any]:
        return _result(eng.entry_points(kind=kind, limit=limit), eng)

    @router.get("/contracts", dependencies=guarded)
    def contracts(
        service: str | None = None,
        kind: str | None = None,
        intent: str | None = None,
        limit: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        return _result(
            eng.contract_map(service=service, kind=kind, intent=intent,
                             limit=limit),
            eng,
        )

    @router.get("/paths", dependencies=guarded)
    def paths(
        start: str,
        goal: str,
        intent: str | None = None,
        limit: int | None = None,
        max_depth: int | None = None,
        max_paths: int | None = None,
        eng: QueryEngine = dep,
    ) -> dict[str, Any]:
        return _result(
            eng.find_paths(start, goal, intent=intent, limit=limit,
                           max_depth=max_depth, max_paths=max_paths),
            eng,
        )

    @router.get("/explain", dependencies=guarded)
    def explain(src: str, dst: str, eng: QueryEngine = dep) -> dict[str, Any]:
        return _result(eng.explain(src, dst), eng)

    @router.get("/coverage", dependencies=guarded)
    def coverage(
        scope: str | None = None, eng: QueryEngine = dep
    ) -> dict[str, Any]:
        return _result(eng.coverage_report(scope), eng)

    # ---- declared, not yet answerable ------------------------------------

    @router.get("/resiliency", dependencies=guarded)
    def resiliency() -> dict[str, Any]:
        raise _not_implemented("resiliency risk signals", "#60")

    @router.get("/dead-code", dependencies=guarded)
    def dead_code() -> dict[str, Any]:
        raise _not_implemented("dead-code detection", "#49")

    @router.get("/reachability/{symbol_id:path}", dependencies=guarded)
    def reachability(symbol_id: str) -> dict[str, Any]:
        raise _not_implemented("reachability verdicts", "#49")

    return router


def create_app(project: Path, api_key: str | None = None) -> Any:
    """A FastAPI app serving one project, for ``lineagelens serve``."""
    from fastapi import FastAPI

    app = FastAPI(
        title="LineageLens",
        description="Deterministic code graph over HTTP.",
        version="1.0.0",
    )
    app.include_router(create_router(project, api_key=api_key))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "project": str(Path(project).resolve())}

    return app


__all__ = [
    "GRAPH_VIEW_LIMIT",
    "SYMBOL_SUBRESOURCES",
    "UNSUPPORTED_NODE_FIELDS",
    "EdgeView",
    "GraphView",
    "NodeView",
    "create_app",
    "create_router",
]
