"""The HTTP surface (#55).

The defect this file exists to prevent is not a wrong answer, it is silence.
``rest.py`` was orphaned by ``59eb115``: it kept importing five deleted modules,
so ``import lineagelens.rest`` raised ``ModuleNotFoundError`` and every route
was dead -- while CI stayed green for months, because nothing in the package or
the tests imported it. The first test below is therefore the most valuable one
in the file, and it is one line.

Beyond that, three properties the surface has to hold:

* it answers from the same engine the CLI and MCP use, so it cannot drift;
* every answer carries the coverage envelope;
* a capability with no producer returns 501, never an empty 200.
"""

from __future__ import annotations

import urllib.parse

import pytest

from lineagelens.indexer import Indexer

fastapi = pytest.importorskip("fastapi", reason="needs lineagelens[rest]")
from fastapi.testclient import TestClient  # noqa: E402

PROJECT = {
    "pyproject.toml": '[project]\nname = "shop"\n',
    "app/repo.py": '''
class Repository:
    def save(self, amount, currency):
        self.total = amount
        return self.total
''',
    "app/service.py": '''
from app.repo import Repository


class OrderService:
    def submit(self, repo: Repository, amount):
        receipt = repo.save(amount, "USD")
        return receipt

    def resubmit(self, repo: Repository, amount):
        return self.submit(repo, amount)
''',
    "app/routes.py": '''
from app.service import OrderService


@router.post("/api/orders")
def create_order(svc: OrderService, repo, amount):
    return svc.submit(repo, amount)
''',
}


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    root = tmp_path_factory.mktemp("rest_shop")
    for rel, text in PROJECT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    store, _ = Indexer(root).run()
    store.close()
    return root


@pytest.fixture(scope="module")
def client(indexed):
    from lineagelens.rest import create_app

    return TestClient(create_app(indexed))


@pytest.fixture(scope="module")
def keyed_client(indexed):
    from lineagelens.rest import create_app

    return TestClient(create_app(indexed, api_key="secret"))


def a_qualified_name(client, needle):
    """The qualified name of one symbol, as the engine reports it."""
    hits = client.get("/api/v1/search", params={"text": needle}).json()["results"]
    exact = [h for h in hits if h.get("node", "").endswith("/" + needle)]
    assert exact, f"{needle!r} not found in {[h.get('node') for h in hits]}"
    return exact[0]["node"]


def strip_transport_additions(payload):
    """Remove what the HTTP layer adds, leaving the engine's own answer.

    Only ``node_id`` / ``target_id`` (#67) and the ``note`` hint. Kept as one
    function so a future addition has to be added here deliberately, rather
    than quietly widening what the route is permitted to invent.
    """
    if isinstance(payload, dict):
        return {
            k: strip_transport_additions(v) for k, v in payload.items()
            if k not in ("node_id", "target_id", "note")
        }
    if isinstance(payload, list):
        return [strip_transport_additions(v) for v in payload]
    return payload


def sym(client, needle):
    """A URL-safe path segment for one symbol.

    Schema 4 names contain ``/`` and ``#``; ``#`` unencoded is a fragment the
    server never sees. Every test that puts an id in a path goes through here,
    which is also what the frontend's ``encodeURIComponent`` does.
    """
    return urllib.parse.quote(a_qualified_name(client, needle), safe="")


# ---------------------------------------------------------------------------
# the regression that motivated the issue
# ---------------------------------------------------------------------------


class TestTheModuleImports:
    def test_importing_rest_does_not_raise(self):
        """The whole point.

        For as long as no test imported this module, it could reference deleted
        modules indefinitely without CI noticing. This single line is what
        makes that impossible to repeat.
        """
        import lineagelens.rest  # noqa: F401

    def test_no_import_of_the_schema_3_core(self):
        """The five modules ``59eb115`` deleted must not come back by name."""
        from pathlib import Path

        import lineagelens.rest as mod

        source = Path(mod.__file__).read_text()
        imports = [
            line for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        for gone in (
            ".analyzer", ".config", ".index", ".queries", ".reachability",
        ):
            offenders = [line for line in imports if gone in line]
            assert not offenders, f"rest.py still imports {gone}: {offenders}"

    def test_the_app_builds_and_is_healthy(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# answers come from the engine, not from a second implementation
# ---------------------------------------------------------------------------


class TestAnswersMatchTheEngine:
    def test_search_returns_results_and_coverage(self, client):
        body = client.get("/api/v1/search", params={"text": "submit"}).json()
        assert body["returned"] > 0
        assert "coverage" in body

    def test_symbol_detail(self, client):
        name = a_qualified_name(client, "save")
        body = client.get(f"/api/v1/symbols/{sym(client, 'save')}").json()
        assert body["results"][0]["node"] == name

    def test_callers_and_callees_disagree(self, client):
        """Forward and backward are different questions, not a flag on one."""
        submit = sym(client, "submit")
        callers = client.get(f"/api/v1/symbols/{submit}/callers").json()
        callees = client.get(f"/api/v1/symbols/{submit}/callees").json()
        assert callers["kind"] != callees["kind"]
        assert callers["results"] != callees["results"]

    def test_lineage_direction_is_honoured(self, client):
        """The frontend's URL shape, answered with real distances."""
        submit = sym(client, "submit")
        back = client.get(
            f"/api/v1/symbols/{submit}/lineage", params={"direction": "backward"}
        ).json()
        fwd = client.get(
            f"/api/v1/symbols/{submit}/lineage", params={"direction": "forward"}
        ).json()
        assert back["direction"] == "backward"
        assert fwd["direction"] == "forward"
        assert back["results"] != fwd["results"]

    def test_lineage_rejects_an_invalid_direction(self, client):
        submit = sym(client, "submit")
        response = client.get(
            f"/api/v1/symbols/{submit}/lineage", params={"direction": "sideways"}
        )
        assert response.status_code == 422

    def test_impact_matches_the_engine_exactly(self, client, indexed):
        """The route must not be a second traversal.

        Compared for equality once the transport's own additions are stripped.
        REST adds a ``node_id`` beside every symbol name (#67) so an answer can
        be turned into the next URL without encoding; that is a projection
        concern and the only thing the route is allowed to add. Everything
        else must come from the engine verbatim, or the two surfaces can drift.
        """
        from lineagelens.query import QueryEngine

        name = a_qualified_name(client, "submit")
        over_http = client.get(
            f"/api/v1/symbols/{sym(client, 'submit')}/impact"
        ).json()

        engine = QueryEngine.open(indexed)
        try:
            direct = engine.impact_of(name).as_dict()
        finally:
            engine.store.close()
        assert strip_transport_additions(over_http) == direct

    def test_unknown_symbol_is_404_not_an_empty_200(self, client):
        """The engine reports a miss in the payload; HTTP says it in the status."""
        response = client.get("/api/v1/symbols/app.nope.nothing")
        assert response.status_code == 404

    def test_coverage_and_entry_points_answer(self, client):
        assert client.get("/api/v1/coverage").status_code == 200
        assert client.get("/api/v1/entry-points").status_code == 200

    def test_path_routes_are_not_shadowed_by_the_symbol_route(self, client):
        """``{symbol_id:path}`` matches greedily; ordering is load-bearing.

        If the bare symbol route were registered first it would swallow
        ``/callers`` as part of the id and this would 404.
        """
        save = sym(client, "save")
        assert client.get(f"/api/v1/symbols/{save}/callers").status_code == 200


# ---------------------------------------------------------------------------
# the graph view
# ---------------------------------------------------------------------------


class TestGraphView:
    def test_returns_nodes_edges_and_its_own_bound(self, client):
        body = client.get("/api/v1/graph/view").json()
        assert body["nodes"], "no nodes rendered"
        assert body["returned"] == len(body["nodes"])
        assert body["truncated"] is (body["total_available"] > body["returned"])

    def test_edges_are_induced_on_the_returned_nodes(self, client):
        """Cytoscape needs both endpoints; a dangling edge is a render error."""
        body = client.get("/api/v1/graph/view").json()
        ids = {n["id"] for n in body["nodes"]}
        dangling = [
            e for e in body["edges"]
            if e["source"] not in ids or e["target"] not in ids
        ]
        assert not dangling, f"{len(dangling)} edges point outside the slice"

    def test_parents_always_resolve(self, client):
        body = client.get("/api/v1/graph/view").json()
        ids = {n["id"] for n in body["nodes"]}
        assert not [
            n for n in body["nodes"] if n["parent"] and n["parent"] not in ids
        ]

    def test_the_limit_truncates_and_says_so(self, client):
        body = client.get("/api/v1/graph/view", params={"limit": 3}).json()
        assert body["returned"] <= 3
        assert body["truncated"] is True
        assert body["total_available"] > 3

    def test_unsupported_fields_are_null_not_false(self, client):
        """A field with no producer must not read as a computed negative.

        ``has_resiliency_flag: false`` would mean "analysed, no risk". There is
        no analysis yet, so it is null and named in ``unsupported``.
        """
        body = client.get("/api/v1/graph/view").json()
        assert "has_resiliency_flag" in body["unsupported"]
        assert all(n["has_resiliency_flag"] is None for n in body["nodes"])
        assert all(n["verdict"] is None for n in body["nodes"])

    def test_module_filter_narrows_the_slice(self, client):
        everything = client.get("/api/v1/graph/view").json()
        scoped = client.get(
            "/api/v1/graph/view", params={"module": "app/repo.py"}
        ).json()
        assert 0 < scoped["total_available"] < everything["total_available"]

    def test_carries_a_coverage_envelope(self, client):
        body = client.get("/api/v1/graph/view").json()
        assert "complete" in body["coverage"]


# ---------------------------------------------------------------------------
# capabilities that do not exist yet
# ---------------------------------------------------------------------------


class TestDeclaredButNotAnswerable:
    @pytest.mark.parametrize(
        ("path", "issue"),
        [
            ("/api/v1/resiliency", "#60"),
            ("/api/v1/dead-code", "#49"),
            ("/api/v1/reachability/anything", "#49"),
        ],
    )
    def test_501_naming_the_tracking_issue(self, client, path, issue):
        """501, not an empty 200 and not a 404.

        An empty list would be indistinguishable from "analysed, found
        nothing", which is the exact confusion the coverage envelope exists to
        prevent. A 404 would say the URL is wrong, which it is not.
        """
        response = client.get(path)
        assert response.status_code == 501
        assert issue in response.json()["detail"]["reason"]

    def test_analyze_and_module_overview_are_gone(self, client):
        """Indexing over HTTP, and a route with no schema-4 equivalent."""
        assert client.post("/api/v1/analyze").status_code in (404, 405)
        assert client.get("/api/v1/modules/app/overview").status_code == 404

    def test_an_unencoded_id_resolves_the_wrong_symbol_but_says_so(self, client):
        """The hazard, and the hint that makes it survivable (#67).

        Every schema-4 qualified name contains ``#``, which is a URL fragment.
        A client that forgets ``encodeURIComponent`` sends only the part before
        it, so the server receives a valid request for the *module* and answers
        it correctly. The request that arrives is indistinguishable from one
        that meant the module, so it cannot be rejected -- but it can be
        annotated, and a hint on a correct answer costs nothing next to
        silence on a possibly wrong one.
        """
        name = a_qualified_name(client, "save")
        assert "#" in name, "the hazard depends on # being in the name"

        unencoded = client.get(f"/api/v1/symbols/{name}").json()
        encoded = client.get(f"/api/v1/symbols/{sym(client, 'save')}").json()

        assert encoded["results"][0]["node"] == name
        # The unencoded form answers about the module, not the method.
        assert unencoded["results"][0]["node"] == name.split("#")[0]
        assert unencoded != encoded
        assert "percent-encode" in unencoded["note"]
        assert "note" not in encoded, "a correct request must not be annotated"

    def test_subresource_routes_are_registered_before_the_bare_route(self, client):
        """The mechanism that stops ``/callers`` being read as part of an id.

        ``{symbol_id:path}`` matches greedily, so registration order is the
        only thing keeping the sub-resources reachable. A refactor that sorts
        the route definitions alphabetically would break every one of them and
        pass every other test in this file.
        """
        from lineagelens.rest import SYMBOL_SUBRESOURCES, create_router

        paths = [r.path for r in create_router(".").routes]
        bare = paths.index("/api/v1/symbols/{symbol_id:path}")
        for suffix in SYMBOL_SUBRESOURCES:
            position = paths.index(f"/api/v1/symbols/{{symbol_id:path}}/{suffix}")
            assert position < bare, f"/{suffix} is registered after the bare route"


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


class TestApiKey:
    def test_missing_key_is_rejected(self, keyed_client):
        assert keyed_client.get("/api/v1/coverage").status_code == 401

    def test_wrong_key_is_rejected(self, keyed_client):
        response = keyed_client.get(
            "/api/v1/coverage", headers={"X-API-Key": "nope"}
        )
        assert response.status_code == 401

    def test_correct_key_is_accepted(self, keyed_client):
        response = keyed_client.get(
            "/api/v1/coverage", headers={"X-API-Key": "secret"}
        )
        assert response.status_code == 200

    def test_the_key_guards_every_route(self):
        """A route added without the dependency is the way auth regresses."""
        from lineagelens.rest import create_router

        router = create_router(".", api_key="secret")
        unguarded = [
            r.path for r in router.routes if not getattr(r, "dependencies", None)
        ]
        assert not unguarded, f"routes without the api-key dependency: {unguarded}"


# ---------------------------------------------------------------------------
# missing or stale index
# ---------------------------------------------------------------------------


class TestNoIndex:
    def test_unindexed_project_is_404_with_the_remedy(self, tmp_path):
        from lineagelens.rest import create_app

        client = TestClient(create_app(tmp_path))
        response = client.get("/api/v1/coverage")
        assert response.status_code == 404
        assert "lineagelens index" in response.json()["detail"]


# ---------------------------------------------------------------------------
# node_id, the URL-safe identifier (#67)
# ---------------------------------------------------------------------------


class TestNodeIdRoundTrip:
    """A client should never have to encode anything.

    Node ids are content hashes with no URL-special characters, so the fix for
    the truncation hazard is not validation -- it is handing the safe form back
    in every answer, so the next URL can be built from the last one.
    """

    def test_search_results_carry_node_id(self, client):
        first = client.get(
            "/api/v1/search", params={"text": "save"}
        ).json()["results"][0]
        assert "node_id" in first
        assert "#" not in first["node_id"]
        assert "/" not in first["node_id"]

    @pytest.mark.parametrize("suffix", ["", "/callers", "/callees", "/impact",
                                        "/dataflow", "/lineage"])
    def test_a_raw_node_id_works_on_every_route(self, client, suffix):
        """No encoding, no escaping, no `#` to lose."""
        node_id = client.get(
            "/api/v1/search", params={"text": "save"}
        ).json()["results"][0]["node_id"]
        assert client.get(f"/api/v1/symbols/{node_id}{suffix}").status_code == 200

    def test_the_round_trip_returns_the_same_node(self, client):
        first = client.get(
            "/api/v1/search", params={"text": "save"}
        ).json()["results"][0]
        again = client.get(
            f"/api/v1/symbols/{first['node_id']}"
        ).json()["results"][0]
        assert again["node"] == first["node"]

    def test_nested_shapes_are_annotated_too(self, client):
        """The reason the annotator walks the payload instead of projecting.

        ``callers_of`` returns paths with a ``hops`` list and ``impact_of``
        nests dependents under three keys. A per-primitive projection is where
        the next shape gets forgotten.
        """
        node_id = client.get(
            "/api/v1/search", params={"text": "submit"}
        ).json()["results"][0]["node_id"]

        hop = client.get(
            f"/api/v1/symbols/{node_id}/callers"
        ).json()["results"][0]["hops"][0]
        assert hop["node_id"]

        impact = client.get(
            f"/api/v1/symbols/{node_id}/impact"
        ).json()["results"][0]
        assert impact["target_id"]
        assert all(d["node_id"] for d in impact.get("in_process", []))

    def test_an_unresolvable_name_gets_no_null_id(self, client):
        """Absence, not a falsy value a caller has to special-case."""
        from lineagelens.rest import _annotate_ids

        annotated = _annotate_ids(
            {"node": "nothing/here", "results": []}, lambda _name: None
        )
        assert "node_id" not in annotated

    def test_control_characters_are_rejected(self, client):
        """No correct client produces these; a fuzzy match would be worse."""
        response = client.get("/api/v1/symbols/bad%00id")
        assert response.status_code == 400
        assert "control characters" in response.json()["detail"]["error"]

    def test_a_legitimate_module_query_is_not_annotated_forever(self, client):
        """The note fires on shape, not on every container.

        Asked with a `#` in it, an id is unambiguous and must not be second
        guessed.
        """
        member = sym(client, "save")
        assert "note" not in client.get(f"/api/v1/symbols/{member}").json()
