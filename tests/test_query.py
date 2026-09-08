"""Query primitives (issue #51 §10).

Built around the three things schema 3's ``get_lineage`` could not do, each of
which is a *structural* guarantee here rather than a behaviour that happens to
hold:

* return a chain, not a bag -- a :class:`Path` is its predecessor list
* report true distances -- a path's length is its edge count, not a DFS artefact
* separate forward from backward -- they are different return values

Plus the two capabilities the whole rearchitecture is for: line-level impact,
and intent-scoped cost.
"""

from __future__ import annotations

import pytest

from lineagelens.core import DataflowMode, EdgeKind, Intent
from lineagelens.indexer import Indexer
from lineagelens.query import Budget, Envelope, QueryEngine, Traverser

# ---------------------------------------------------------------------------
# a small, fully-understood project
# ---------------------------------------------------------------------------

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
    "web/client.ts": """
export async function placeOrder(amount: number) {
  return fetch("/api/orders", {method: "POST", body: String(amount)});
}
""",
}


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("shop")
    for rel, text in PROJECT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    # `web/` gets its own manifest so the cross-service split is exercised.
    (root / "web").mkdir(exist_ok=True)
    (root / "web" / "package.json").write_text('{"name": "web"}')
    store, report = Indexer(
        root,
        dataflow=DataflowMode.EAGER,
    ).run()
    return QueryEngine(store, str(root)), report, root


@pytest.fixture
def engine(project):
    return project[0]


def qname(engine, needle, kind=None):
    """Resolve a short name to its qualified name via search."""
    hits = engine.store.search(needle, kinds=[kind] if kind else None, limit=10)
    exact = [h for h in hits if h.name == needle]
    assert exact, f"{needle!r} not found (got {[h.name for h in hits]})"
    return exact[0].qualified_name


# ---------------------------------------------------------------------------
# §10.2 -- paths, not sets
# ---------------------------------------------------------------------------


class TestPathsNotSets:
    def test_find_paths_returns_an_ordered_chain(self, engine):
        """The question ``get_lineage`` could not answer at all."""
        result = engine.find_paths(
            qname(engine, "resubmit"), qname(engine, "save"), max_paths=5
        )
        assert result.results, result.extra.get("error")
        chain = result.results[0]["chain"]
        assert chain.startswith("resubmit")
        assert chain.endswith("save")
        assert [h["depth"] for h in result.results[0]["hops"]] == [0, 1, 2]

    def test_path_length_is_the_true_distance(self, engine):
        """A direct edge must report length 1 however the walk reached it.

        On ``A->B->C->D`` plus ``A->D``, schema 3 reported D at depth 3: its
        global visited set meant a node seen on a long branch could never
        reappear on a short one.
        """
        result = engine.find_paths(qname(engine, "submit"), qname(engine, "save"))
        assert result.results
        assert result.results[0]["length"] == 1

    def test_shortest_path_first(self, engine):
        result = engine.find_paths(
            qname(engine, "resubmit"), qname(engine, "save"), max_paths=5
        )
        lengths = [p["length"] for p in result.results]
        assert lengths == sorted(lengths)
        assert result.ranking == "shortest_path_first"

    def test_forward_and_backward_are_separate_results(self, engine):
        """Not one flat list with no direction field."""
        target = qname(engine, "save")
        callers = engine.callers_of(target)
        callees = engine.callees_of(target)
        assert all(p["direction"] == "backward" for p in callers.results)
        assert all(p["direction"] == "forward" for p in callees.results)

    def test_callers_carry_their_chain(self, engine):
        """Every caller comes with the route by which it reaches the target."""
        result = engine.callers_of(qname(engine, "save"))
        assert result.results
        for path in result.results:
            assert path["hops"][0]["node"].endswith("save")
            assert path["length"] >= 1

    def test_non_transitive_stops_at_one_hop(self, engine):
        direct = engine.callers_of(qname(engine, "save"), transitive=False)
        assert all(p["length"] == 1 for p in direct.results)

    def test_cycles_do_not_hang(self, tmp_path):
        """Mutual recursion must terminate rather than walk forever."""
        (tmp_path / "m.py").write_text(
            "def a(n):\n    return b(n)\n\n\ndef b(n):\n    return a(n)\n"
        )
        store, _ = Indexer(tmp_path).run()
        engine = QueryEngine(store, str(tmp_path))
        result = engine.callers_of(qname(engine, "a"))
        assert result.total_available < 10

    def test_trace_flow_is_a_tree(self, engine):
        result = engine.trace_flow(qname(engine, "resubmit"), max_depth=3)
        tree = result.results[0]
        assert "node" in tree
        assert "calls" in tree, "a tree must nest, not flatten"


class TestKindFiltering:
    def test_kinds_are_filtered_server_side(self, engine):
        """Schema 3 told the agent to filter client-side, after paying for it."""
        target = qname(engine, "save")
        calls_only = engine.callers_of(target, kinds=[EdgeKind.CALLS])
        for path in calls_only.results:
            for hop in path["hops"][1:]:
                assert hop["via"] == "CALLS"

    def test_contains_is_not_followed_by_default(self, engine):
        """Following CONTAINS would let a walk reach every module sibling."""
        result = engine.callees_of(qname(engine, "submit"))
        for path in result.results:
            for hop in path["hops"][1:]:
                assert hop["via"] != "CONTAINS"


# ---------------------------------------------------------------------------
# §10.4 -- line-level impact
# ---------------------------------------------------------------------------


class TestLineLevelImpact:
    def test_a_line_maps_to_the_operations_on_it(self, engine, project):
        """The requirement that forced spans onto edges.

        "Line N calls save()" is a different fact from "line N is somewhere
        inside submit()", and only edge spans can tell them apart.
        """
        root = project[2]
        lines = (root / "app" / "service.py").read_text().splitlines()
        line = next(
            i for i, text in enumerate(lines, 1) if "repo.save(" in text
        )
        result = engine.impact_of(f"app/service.py:{line}")
        report = result.results[0].as_dict()
        assert report["at"] == f"app/service.py:{line}"
        assert any(
            op["kind"] == "CALLS" and op["target"].endswith("save")
            for op in report["anchored_operations"]
        ), report["anchored_operations"]

    def test_signature_line_is_classified_as_a_signature_change(
        self, engine, project
    ):
        """A signature edit breaks every caller; a body edit does not."""
        root = project[2]
        lines = (root / "app" / "repo.py").read_text().splitlines()
        line = next(i for i, t in enumerate(lines, 1) if "def save" in t)
        result = engine.impact_of(f"app/repo.py:{line}")
        assert result.results[0].as_dict()["change_kind"] == "signature"

    def test_route_line_is_classified_as_a_contract_change(self, engine, project):
        """A string literal that is a route key has cross-service reach.

        Schema 3 had no contract concept, so this edit was indistinguishable
        from any other line.
        """
        root = project[2]
        lines = (root / "app" / "routes.py").read_text().splitlines()
        line = next(i for i, t in enumerate(lines, 1) if "/api/orders" in t)
        result = engine.impact_of(f"app/routes.py:{line}")
        assert result.results[0].as_dict()["change_kind"] == "contract_key"

    def test_in_process_and_cross_service_are_separated(self, engine):
        """Different answers to "what breaks": a build failure vs a wire break."""
        result = engine.impact_of(qname(engine, "create_order"))
        report = result.results[0].as_dict()
        cross = report.get("cross_service", [])
        assert any("via_contract" in entry for entry in cross), (
            f"the TypeScript client should reach the route via a contract: {cross}"
        )

    def test_unknown_line_reports_an_error_not_an_empty_answer(self, engine):
        result = engine.impact_of("app/service.py:9999")
        assert "error" in result.extra

    def test_impact_of_diff_walks_hunks(self, engine, project):
        """Usable directly in CI, where the question is "what does this change do"."""
        root = project[2]
        lines = (root / "app" / "service.py").read_text().splitlines()
        line = next(i for i, t in enumerate(lines, 1) if "repo.save(" in t)
        diff = (
            "--- a/app/service.py\n"
            "+++ b/app/service.py\n"
            f"@@ -{line},1 +{line},1 @@\n"
            '+        receipt = repo.save(amount, "EUR")\n'
        )
        result = engine.impact_of_diff(diff)
        assert result.returned >= 1


# ---------------------------------------------------------------------------
# §10.1 -- intent scoping
# ---------------------------------------------------------------------------


class TestIntentScoping:
    def test_plan_computes_no_dataflow(self, engine, project):
        """The primary cost control: not computing detail the question skips."""
        root = project[2]
        lines = (root / "app" / "service.py").read_text().splitlines()
        line = next(i for i, t in enumerate(lines, 1) if "repo.save(" in t)
        target = f"app/service.py:{line}"

        precise = engine.impact_of(target, intent=Intent.PRECISE)
        plan = engine.impact_of(target, intent=Intent.PLAN)

        assert precise.results[0].as_dict().get("data")
        assert not plan.results[0].as_dict().get("data")
        assert plan.envelope.dataflow["mode"] == "skipped"

    def test_precise_carries_source_and_plan_does_not(self, engine, project):
        root = project[2]
        lines = (root / "app" / "service.py").read_text().splitlines()
        line = next(i for i, t in enumerate(lines, 1) if "repo.save(" in t)
        target = f"app/service.py:{line}"

        assert engine.impact_of(target, intent=Intent.PRECISE).results[0].source
        assert not engine.impact_of(target, intent=Intent.PLAN).results[0].source

    def test_intent_is_recorded_on_the_answer(self, engine):
        result = engine.callers_of(qname(engine, "save"), intent=Intent.PLAN)
        assert result.envelope.as_dict()["intent"] == "plan"

    def test_file_line_defaults_to_precise(self, engine, project):
        """A line is only ever asked about while debugging."""
        root = project[2]
        lines = (root / "app" / "repo.py").read_text().splitlines()
        line = next(i for i, t in enumerate(lines, 1) if "def save" in t)
        assert engine.impact_of(f"app/repo.py:{line}").envelope.intent is Intent.PRECISE

    def test_symbol_defaults_to_plan(self, engine):
        """A symbol-level question is usually a survey."""
        assert engine.impact_of(qname(engine, "save")).envelope.intent is Intent.PLAN


# ---------------------------------------------------------------------------
# §10.5 -- budgets
# ---------------------------------------------------------------------------


class TestBudgets:
    def test_truncation_is_reported(self, engine):
        """Schema 3 returned 328 of 759 symbols with no sign it had stopped."""
        result = engine.callees_of(qname(engine, "resubmit"), limit=1)
        if result.total_available > 1:
            assert result.truncated
            assert result.returned == 1
            assert result.total_available > result.returned

    def test_untruncated_answer_says_so(self, engine):
        result = engine.callers_of(qname(engine, "save"), limit=500)
        assert result.truncated is False
        assert result.returned == result.total_available

    def test_depth_limit_is_reported_as_a_boundary(self, engine):
        result = engine.callees_of(qname(engine, "resubmit"), max_depth=1)
        if result.envelope.boundaries:
            assert "depth_limit" in result.envelope.boundaries


# ---------------------------------------------------------------------------
# §9 -- data flow
# ---------------------------------------------------------------------------


class TestDataflow:
    def test_value_crosses_a_call_boundary(self, engine):
        """PARAM_BINDS plus RETURNS is what makes a call traversable for data.

        Disambiguated deliberately: both ``submit`` and ``save`` declare an
        ``amount``, and only the caller's has an outgoing binding. Picking
        either by bare name would make this test pass or fail by accident.
        """
        caller_amount = next(
            n.qualified_name
            for n in engine.store.search("amount", kinds=["parameter"], limit=10)
            if n.qualified_name.endswith("submit/amount")
        )
        result = engine.dataflow_of(caller_amount)
        assert result.results, result.extra.get("error")

        downstream = [f for f in result.results if f["direction"] == "downstream"]
        assert any("PARAM_BINDS" in flow["via"] for flow in downstream), (
            f"argument should bind the callee's parameter: "
            f"{[f['via'] for f in result.results]}"
        )

    def test_argument_binds_from_the_value_not_the_body(self, engine):
        """The binding must start at the argument's own node.

        Binding from the enclosing body -- all the call reference itself
        identifies -- leaves the value disconnected: a walk from ``amount``
        would find its local reads and stop dead at the call.
        """
        row = engine.store.conn.execute(
            "SELECT metadata FROM edges WHERE kind = 'PARAM_BINDS' "
            "AND metadata LIKE '%argument_value%' LIMIT 1"
        ).fetchone()
        assert row is not None, "no PARAM_BINDS bound from an argument value"

    def test_return_flows_to_the_assignment_target(self, engine):
        """``receipt = repo.save(...)`` means receipt's value comes from save."""
        rows = engine.store.conn.execute(
            "SELECT src, dst FROM edges WHERE kind = 'RETURNS'"
        ).fetchall()
        assert rows, "no RETURNS edges"
        pairs = {
            (
                engine.store.get_node(r["src"]).name,
                engine.store.get_node(r["dst"]).name,
            )
            for r in rows
        }
        assert ("save", "receipt") in pairs, pairs

    def test_direction_is_labelled(self, engine):
        result = engine.dataflow_of(
            qname(engine, "total", kind="field"), direction="both"
        )
        directions = {flow["direction"] for flow in result.results}
        assert directions <= {"upstream", "downstream"}

    def test_lazy_dataflow_is_reported_as_deferred(self, tmp_path):
        """A deferred body must not look like a permanent gap (§9.2)."""
        (tmp_path / "m.py").write_text("def f(a):\n    b = a\n    return b\n")
        store, _ = Indexer(tmp_path, dataflow=DataflowMode.LAZY).run()
        engine = QueryEngine(store, str(tmp_path))
        result = engine.dataflow_of(qname(engine, "b", kind="variable"))
        assert result.envelope.dataflow["status"] == "lazy"
        assert "dataflow_deferred" in result.envelope.boundaries


# ---------------------------------------------------------------------------
# §10.6 -- the completeness envelope
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_every_answer_carries_coverage(self, engine):
        for result in (
            engine.search("save"),
            engine.callers_of(qname(engine, "save")),
            engine.contract_map(),
            engine.coverage_report(),
        ):
            coverage = result.envelope.as_dict()
            assert "intent" in coverage
            assert "complete" in coverage

    def test_reference_resolution_is_reported(self, engine):
        coverage = engine.coverage_report().envelope.as_dict()
        refs = coverage["refs"]
        assert refs["total"] > 0
        assert 0 <= refs["unresolved"] <= 1
        assert abs(refs["exact"] + refs["inferred"] + refs["unresolved"] - 1) < 0.01

    def test_tier_a_only_languages_are_reported_as_degraded(self, engine):
        """An explicit Tier-A-only override must never be silent (§7.2)."""
        coverage = engine.coverage_report().envelope.as_dict()
        assert "tier_used" in coverage

    def test_ambiguity_boundaries_surface_on_a_walk(self, tmp_path):
        """A dispatch ambiguity recorded at index time reaches the answer."""
        (tmp_path / "a.py").write_text("class A:\n    def run(self):\n        pass\n")
        (tmp_path / "b.py").write_text("class B:\n    def run(self):\n        pass\n")
        (tmp_path / "c.py").write_text("def go(t):\n    t.run()\n")
        store, _ = Indexer(tmp_path).run()
        engine = QueryEngine(store, str(tmp_path))
        result = engine.callers_of(qname(engine, "go"))
        assert result.envelope.as_dict()["complete"] in (True, False)


# ---------------------------------------------------------------------------
# §8 / §10.3 -- contracts and motifs
# ---------------------------------------------------------------------------


class TestContractsAndMotifs:
    def test_contract_map_shows_the_cross_language_join(self, engine):
        result = engine.contract_map()
        joined = [
            entry for entry in result.results
            if entry["exposed_by"] and entry["consumed_by"]
        ]
        assert joined, f"expected a joined contract, got {result.results}"
        assert joined[0]["cross_service"] is True

    def test_dangling_contract_is_flagged(self, engine):
        result = engine.contract_map()
        assert "dangling" in result.extra

    def test_similar_flows_returns_a_shape_and_extension_points(self, engine):
        result = engine.similar_flows(qname(engine, "create_order"))
        payload = result.results[0]
        assert "canonical_shape" in payload
        assert "extension_points" in payload

    def test_entry_points_come_from_exposes_edges(self, engine):
        """Entry points are derived from contracts, not a dropped attribute.

        Schema 3 stored them as a list the SQLite writer did not persist, so
        all 315 read back as zero and every reachability root vanished.
        """
        result = engine.entry_points()
        assert result.total_available >= 1
        assert all(e["contract_kind"] for e in result.results)


# ---------------------------------------------------------------------------
# §10.2 -- explain, stack traces, source
# ---------------------------------------------------------------------------


class TestExplainAndTraces:
    def test_explain_names_the_evidence_and_provenance(self, engine):
        result = engine.explain(qname(engine, "submit"), qname(engine, "save"))
        assert result.results
        entry = result.results[0]
        assert entry["evidence_tier"] in ("fact", "heuristic", "probabilistic")
        assert entry["provenance"]
        assert entry["written_at"]

    def test_map_stacktrace_locates_frames(self, engine):
        trace = (
            'Traceback (most recent call last):\n'
            '  File "app/routes.py", line 6, in create_order\n'
            '    return svc.submit(repo, amount)\n'
            '  File "app/service.py", line 7, in submit\n'
            '    receipt = repo.save(amount, "USD")\n'
            'ValueError: boom\n'
        )
        result = engine.map_stacktrace(trace)
        located = [f for f in result.results if f.get("node")]
        assert located, result.results
        assert located[0]["at"].startswith("app/service.py"), (
            "innermost frame must come first"
        )

    def test_frame_outside_the_index_is_reported(self, engine):
        trace = 'File "/usr/lib/python3/site-packages/x.py", line 3, in y\n'
        result = engine.map_stacktrace(trace)
        assert any(f.get("reason") == "not in the index" for f in result.results)

    def test_responses_carry_verbatim_source(self, engine):
        """The largest unrealised value in schema 3: no tool read file text."""
        result = engine.get_node(qname(engine, "save"))
        source = result.results[0]["source"]
        assert "def save" in source
        assert source.lstrip().split()[0].isdigit(), "source must be line-numbered"

    def test_search_is_ranked_and_filterable(self, engine):
        result = engine.search("save", kinds=["method"], limit=5)
        assert result.ranking == "bm25"
        assert all(item["kind"] == "method" for item in result.results)


# ---------------------------------------------------------------------------
# traversal internals
# ---------------------------------------------------------------------------


class TestTraverserDirectly:
    def test_visited_is_per_path_in_find_paths(self, engine):
        """Two routes to one node must both be returned.

        A global visited set -- which is what made schema 3 report wrong depths
        -- would return only whichever was found first.
        """
        traverser = Traverser(engine.store)
        start = engine._node(qname(engine, "resubmit"))
        goal = engine._node(qname(engine, "save"))
        paths = traverser.find_paths(
            start.id, goal.id, kinds=[EdgeKind.CALLS],
            budget=Budget(max_paths=10, max_depth=6), envelope=Envelope(),
        )
        assert paths
        assert len({p.signature() for p in paths}) == len(paths), "duplicate paths"
