"""Schema-4 store guarantees (issue #51 §5, §16).

The tests are organised around the acceptance criteria rather than around
methods, because each one corresponds to a measured schema-3 defect:

* lossless persistence -- schema 3 kept 9 of ~25 node attributes, discarding 315
  entry points and 3,326 argument lists on every write (§1.4)
* zero dangling endpoints -- on Apache Dubbo all 2,593 edges had an unresolvable
  source, making the graph 100% non-traversable while reporting success (§1.2)
* balanced coverage -- unresolved references were dropped entirely, so the graph
  claimed ``resolved`` on every surviving edge (§1.2)
* schema refusal -- preferring a lossy store over a complete one, with only a
  debug log line, is how the above stayed invisible (§1.4)
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from lineagelens.core import (
    SCHEMA_VERSION,
    Boundary,
    BoundaryKind,
    Contract,
    ContractKind,
    Coverage,
    DataflowStatus,
    Edge,
    EdgeKind,
    Evidence,
    EvidenceTier,
    ExtractorRun,
    FileRecord,
    Node,
    NodeFlags,
    NodeKind,
    ParseError,
    ParseStatus,
    RefKind,
    RefStatus,
    Resolution,
    Service,
    SkipReason,
    Span,
    Tier,
    UnresolvedRef,
    Visibility,
    contract_id,
    node_id,
    qualified_name,
    service_id,
    signature_hash,
)
from lineagelens.store import GraphNotFound, GraphStore, SchemaMismatch

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    st = GraphStore.create(tmp_path / "graph.sqlite", project_root=str(tmp_path))
    yield st
    st.close()


@pytest.fixture
def svc():
    return Service(id=service_id("api", "."), name="api", root=".", kind="pypi")


def make_file(path="api/orders.py", lang="python", svc_id=None, **kw):
    return FileRecord(
        path=path,
        lang=lang,
        content_hash=kw.pop("content_hash", "hash-1"),
        size_bytes=kw.pop("size_bytes", 512),
        parse_status=kw.pop("parse_status", ParseStatus.OK),
        service_id=svc_id,
        extractors=kw.pop(
            "extractors",
            (ExtractorRun(name="ts-python", version="0.23.4", tier=Tier.A),),
        ),
        **kw,
    )


def make_node(name="submit", members=None, *, kind=NodeKind.METHOD, span=None, **kw):
    members = members or [name]
    qname = qualified_name("api", "python", "app.orders", members)
    sig = kw.pop("signature_hash", "")
    return Node(
        id=node_id("api", "python", qname, sig),
        kind=kind,
        name=name,
        qualified_name=qname,
        lang="python",
        span=span or Span(0, 50, 10, 4, 20, 8),
        file_path=kw.pop("file_path", "api/orders.py"),
        signature_hash=sig,
        **kw,
    )


# ---------------------------------------------------------------------------
# §16.3 -- lossless persistence
# ---------------------------------------------------------------------------


class TestLosslessRoundTrip:
    """Every field an extractor computes must survive a write/read cycle."""

    def test_every_node_field_round_trips(self, store, svc):
        store.write_services([svc])
        fid = store.write_file(make_file(svc_id=svc.id))
        original = Node(
            id=node_id("api", "python", "api/python/app/orders#submit", signature_hash(["Order"])),
            kind=NodeKind.METHOD,
            name="submit",
            qualified_name="api/python/app/orders#submit",
            lang="python",
            span=Span(10, 640, 12, 4, 40, 8),
            file_path="api/orders.py",
            service_id=svc.id,
            signature="(order: Order) -> Receipt",
            signature_hash=signature_hash(["Order"]),
            docstring="Submit an order.",
            return_type="Receipt",
            type_ref=None,
            visibility=Visibility.PUBLIC,
            flags=NodeFlags.ASYNC | NodeFlags.EXPORTED | NodeFlags.ENTRY_POINT,
            type_params=("T", "U"),
            decorators=("@router.post", "@auth"),
            parent_id="parent-id",
        )
        store.write_nodes([original], file_id=fid)

        got = store.get_node(original.id)
        assert got is not None
        for field in (
            "id", "kind", "name", "qualified_name", "lang", "span", "file_path",
            "service_id", "signature", "signature_hash", "docstring", "return_type",
            "type_ref", "visibility", "flags", "type_params", "decorators", "parent_id",
        ):
            assert getattr(got, field) == getattr(original, field), f"{field} was not persisted"

    def test_edge_metadata_and_evidence_round_trip(self, store):
        """``metadata`` carries the argument lists PARAM_BINDS is built from (§9).

        Schema 3 computed 3,326 of these and persisted none, which is why
        parameter binding could not be implemented on top of it.
        """
        fid = store.write_file(make_file())
        a, b = make_node("submit"), make_node("save", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)

        original = Edge(
            src=a.id, dst=b.id, kind=EdgeKind.CALLS,
            evidence=Evidence.fact("javac_resolve"),
            resolution=Resolution.EXACT,
            provenance="javac",
            span=Span(120, 140, 18, 8, 18, 28),
            file_path="api/orders.py",
            metadata={"arg_index": 2, "candidates": ["x", "y"], "variadic": False},
        )
        assert store.write_edges([original], file_id=fid) == 1

        got = store.edges_from(a.id)[0]
        assert got.metadata == original.metadata
        assert got.evidence.tier is EvidenceTier.FACT
        assert got.evidence.label == "javac_resolve"
        assert got.resolution is Resolution.EXACT
        assert got.provenance == "javac"
        assert got.span.start_line == 18

    def test_probabilistic_confidence_round_trips(self, store):
        fid = store.write_file(make_file())
        a, b = make_node("a"), make_node("b", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)
        store.write_edges(
            [Edge(src=a.id, dst=b.id, kind=EdgeKind.REFERENCES,
                  evidence=Evidence(tier=EvidenceTier.PROBABILISTIC, label="llm", confidence=0.75),
                  resolution=Resolution.INFERRED, provenance="llm")],
            file_id=fid,
        )
        assert store.edges_from(a.id)[0].evidence.confidence == 0.75

    def test_file_ledger_round_trips_errors_and_skips(self, store):
        """Skipped and partially-parsed files are ledger entries, not omissions."""
        store.write_file(make_file(
            path="broken.py",
            parse_status=ParseStatus.PARTIAL,
            parse_errors=(ParseError(line=4, col=2, message="unexpected token"),),
        ))
        store.write_file(make_file(
            path="vendor/lib.rs", lang="rust",
            parse_status=ParseStatus.SKIPPED, skip_reason=SkipReason.MISSING_TIER_B,
            extractors=(),
        ))
        by_path = {f.path: f for f in store.files()}
        assert by_path["broken.py"].parse_errors[0].message == "unexpected token"
        assert by_path["vendor/lib.rs"].skip_reason is SkipReason.MISSING_TIER_B
        assert by_path["vendor/lib.rs"].parse_status is ParseStatus.SKIPPED

    def test_unresolved_refs_round_trip(self, store):
        fid = store.write_file(make_file())
        a = make_node("submit")
        store.write_nodes([a], file_id=fid)
        store.write_unresolved([UnresolvedRef(
            from_node=a.id, ref_text="boto3.client", ref_kind=RefKind.CALL,
            span=Span(200, 212, 22, 8, 22, 20), file_path="api/orders.py",
            receiver_hint="module", candidates=("c1", "c2"),
            status=RefStatus.AMBIGUOUS, reason="two candidates",
            metadata={"arg_count": 1},
        )], file_id=fid)

        got = store.unresolved_for_file("api/orders.py")[0]
        assert got.ref_text == "boto3.client"
        assert got.candidates == ("c1", "c2")
        assert got.status is RefStatus.AMBIGUOUS
        assert got.receiver_hint == "module"
        assert got.metadata == {"arg_count": 1}


# ---------------------------------------------------------------------------
# §16.1 -- graph integrity
# ---------------------------------------------------------------------------


class TestGraphIntegrity:
    def test_no_dangling_endpoints(self, store):
        fid = store.write_file(make_file())
        a, b = make_node("a"), make_node("b", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)
        store.write_edges([Edge(src=a.id, dst=b.id, kind=EdgeKind.CALLS,
                                evidence=Evidence.fact("ast"), resolution=Resolution.EXACT,
                                provenance="t")], file_id=fid)
        assert store.dangling_edge_count() == 0

    def test_edge_to_unknown_node_is_refused(self, store):
        """The defect that made the Dubbo graph non-traversable, now a hard error.

        Schema 3 wrote 2,593 edges whose sources matched no symbol id and
        reported success. Foreign keys make that unrepresentable.
        """
        fid = store.write_file(make_file())
        a = make_node("a")
        store.write_nodes([a], file_id=fid)
        with pytest.raises(sqlite3.IntegrityError):
            store.write_edges([Edge(src=a.id, dst="does-not-exist", kind=EdgeKind.CALLS,
                                    evidence=Evidence.fact("ast"),
                                    resolution=Resolution.EXACT, provenance="t")], file_id=fid)

    def test_identical_edge_is_idempotent(self, store):
        """Re-resolving one file must not duplicate its edges."""
        fid = store.write_file(make_file())
        a, b = make_node("a"), make_node("b", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)
        e = Edge(src=a.id, dst=b.id, kind=EdgeKind.CALLS, evidence=Evidence.fact("ast"),
                 resolution=Resolution.EXACT, provenance="t", span=Span(1, 2, 5, 0, 5, 2))
        assert store.write_edges([e], file_id=fid) == 1
        assert store.write_edges([e], file_id=fid) == 0
        assert store.counts()["edges"] == 1

    def test_same_target_on_different_lines_stays_two_edges(self, store):
        """Two call sites are two edges; collapsing them would lose §10.4 precision."""
        fid = store.write_file(make_file())
        a, b = make_node("a"), make_node("b", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)
        mk = lambda span: Edge(  # noqa: E731 - table-driven, reads better inline
            src=a.id, dst=b.id, kind=EdgeKind.CALLS, evidence=Evidence.fact("ast"),
            resolution=Resolution.EXACT, provenance="t", span=span,
        )
        store.write_edges([mk(Span(10, 20, 5, 0, 5, 10)), mk(Span(30, 40, 9, 0, 9, 10))],
                          file_id=fid)
        assert store.counts()["edges"] == 2

    def test_node_written_twice_merges_rather_than_duplicating(self, store):
        """Tier A and Tier B both minting one node is by design (§6).

        The second write must enrich the row, not create a rival, and must not
        double-insert into the FTS mirror.
        """
        fid = store.write_file(make_file())
        bare = make_node("submit")
        store.write_nodes([bare], file_id=fid)
        enriched = replace(bare, signature="(o: Order) -> None",
                           docstring="Submit.", flags=NodeFlags.ASYNC)
        store.write_nodes([enriched], file_id=fid)

        assert store.counts()["nodes"] == 1
        got = store.get_node(bare.id)
        assert got.signature == "(o: Order) -> None"
        assert got.docstring == "Submit."
        assert got.has(NodeFlags.ASYNC)
        assert len(store.search("submit")) == 1

    def test_purge_file_cascades(self, store):
        fid = store.write_file(make_file())
        a, b = make_node("a"), make_node("b", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)
        store.write_edges([Edge(src=a.id, dst=b.id, kind=EdgeKind.CALLS,
                                evidence=Evidence.fact("ast"), resolution=Resolution.EXACT,
                                provenance="t")], file_id=fid)
        store.write_unresolved([UnresolvedRef(from_node=a.id, ref_text="x",
                                              ref_kind=RefKind.CALL, span=Span(1, 2, 3, 0, 3, 1),
                                              file_path="api/orders.py")], file_id=fid)
        store.purge_file("api/orders.py")

        counts = store.counts()
        assert counts["nodes"] == 0
        assert counts["edges"] == 0
        assert counts["unresolved_refs"] == 0
        assert store.search("a") == []


# ---------------------------------------------------------------------------
# §16.5 -- reference accounting
# ---------------------------------------------------------------------------


class TestCoverageAccounting:
    def test_balanced_coverage_is_accepted(self, store):
        fid = store.write_file(make_file())
        store.write_coverage(Coverage(
            file_path="api/orders.py", nodes_found=3, refs_total=10,
            refs_exact=6, refs_inferred=3, refs_unresolved=1,
            boundaries_count=0, dataflow_status=DataflowStatus.COMPUTED,
        ), fid)
        assert store.counts()["coverage"] == 1

    def test_unaccounted_reference_is_rejected_at_construction(self):
        """A dropped reference must be impossible to express, not merely discouraged."""
        with pytest.raises(ValueError, match="refs unaccounted for"):
            Coverage(
                file_path="f.py", nodes_found=1, refs_total=10,
                refs_exact=5, refs_inferred=2, refs_unresolved=1,  # sums to 8
                boundaries_count=0, dataflow_status=DataflowStatus.COMPUTED,
            )

    def test_skipped_file_must_state_a_reason(self):
        """A skip without a reason is an invisible gap, which §7.1 forbids."""
        with pytest.raises(ValueError, match="must record a skip_reason"):
            FileRecord(path="x.kt", lang="kotlin", content_hash="h", size_bytes=1,
                       parse_status=ParseStatus.SKIPPED)


# ---------------------------------------------------------------------------
# §10.4 -- span queries
# ---------------------------------------------------------------------------


class TestSpanQueries:
    def test_nodes_containing_line_orders_outermost_first(self, store):
        fid = store.write_file(make_file())
        cls = make_node("Orders", ["Orders"], kind=NodeKind.CLASS, span=Span(0, 900, 5, 0, 60, 0))
        meth = make_node("submit", ["Orders", "submit"], span=Span(100, 400, 12, 4, 30, 8))
        store.write_nodes([cls, meth], file_id=fid)

        found = store.nodes_containing_line("api/orders.py", 20)
        assert [n.name for n in found] == ["Orders", "submit"]
        assert found[-1].name == "submit", "innermost node must be last -- it is the edit target"

    def test_line_outside_every_span_returns_nothing(self, store):
        fid = store.write_file(make_file())
        store.write_nodes([make_node("submit", span=Span(0, 100, 10, 0, 20, 0))], file_id=fid)
        assert store.nodes_containing_line("api/orders.py", 999) == []

    def test_edges_on_line_finds_the_operation(self, store):
        """"What does line 18 do" -- answerable only because edges carry spans."""
        fid = store.write_file(make_file())
        a, b = make_node("submit"), make_node("save", kind=NodeKind.FUNCTION)
        store.write_nodes([a, b], file_id=fid)
        store.write_edges([Edge(src=a.id, dst=b.id, kind=EdgeKind.CALLS,
                                evidence=Evidence.fact("ast"), resolution=Resolution.EXACT,
                                provenance="t", span=Span(120, 140, 18, 8, 18, 28))], file_id=fid)

        assert [e.kind for e in store.edges_on_line("api/orders.py", 18)] == [EdgeKind.CALLS]
        assert store.edges_on_line("api/orders.py", 19) == []

    def test_edge_kind_filtering(self, store):
        fid = store.write_file(make_file())
        a, b = make_node("a"), make_node("b", kind=NodeKind.CLASS)
        store.write_nodes([a, b], file_id=fid)
        for kind in (EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.HAS_TYPE):
            store.write_edges([Edge(src=a.id, dst=b.id, kind=kind,
                                    evidence=Evidence.fact("ast"), resolution=Resolution.EXACT,
                                    provenance="t", span=Span(kind.value.__len__(), 99, 1, 0, 1, 5))],
                              file_id=fid)
        assert len(store.edges_from(a.id)) == 3
        assert len(store.edges_from(a.id, kinds=[EdgeKind.CALLS])) == 1
        assert len(store.edges_from(a.id, kinds=[EdgeKind.CALLS, EdgeKind.HAS_TYPE])) == 2


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_prefix_match(self, store):
        fid = store.write_file(make_file())
        store.write_nodes([make_node("load_graph", kind=NodeKind.FUNCTION)], file_id=fid)
        assert [n.name for n in store.search("load_gr")] == ["load_graph"]

    def test_punctuation_does_not_raise(self, store):
        """FTS5 treats ``-`` and ``:`` as syntax; a symbol search must not crash."""
        fid = store.write_file(make_file())
        store.write_nodes([make_node("getUser", kind=NodeKind.FUNCTION)], file_id=fid)
        for query in ("get-user", "foo:bar", "a*b", '"unclosed', "-", ""):
            store.search(query)  # must not raise

    def test_docstring_is_searchable(self, store):
        fid = store.write_file(make_file())
        store.write_nodes(
            [make_node("f", kind=NodeKind.FUNCTION, docstring="Reticulates the splines.")],
            file_id=fid,
        )
        assert [n.name for n in store.search("reticulates")] == ["f"]

    def test_kind_and_lang_filters(self, store):
        fid = store.write_file(make_file())
        store.write_nodes([
            make_node("thing", kind=NodeKind.CLASS),
            make_node("thing2", ["thing2"], kind=NodeKind.FUNCTION),
        ], file_id=fid)
        assert len(store.search("thing", kinds=[NodeKind.CLASS])) == 1
        assert len(store.search("thing", lang="python")) == 2
        assert len(store.search("thing", lang="java")) == 0


# ---------------------------------------------------------------------------
# contracts (§8.1)
# ---------------------------------------------------------------------------


class TestContracts:
    def test_contract_stored_as_node_joins_two_languages(self, store):
        """A cross-framework path is a two-hop walk through a normal node.

        Keeping contracts in a side table would force the query layer to special
        case framework boundaries; as nodes, one traversal handles them.
        """
        py_fid = store.write_file(make_file("api/routes.py", "python"))
        ts_fid = store.write_file(make_file("web/api.ts", "typescript"))

        contract = Contract(
            id=contract_id("http_route", "GET /api/users/{*}"),
            kind=ContractKind.HTTP_ROUTE,
            key="GET /api/users/{id}",
            normalised_key="GET /api/users/{*}",
        )
        handler = make_node("get_user", kind=NodeKind.FUNCTION, file_path="api/routes.py")
        caller = Node(
            id=node_id("web", "typescript", "web/typescript/api#fetchUser"),
            kind=NodeKind.FUNCTION, name="fetchUser",
            qualified_name="web/typescript/api#fetchUser", lang="typescript",
            span=Span(0, 40, 3, 0, 6, 1), file_path="web/api.ts",
        )
        store.write_nodes([contract.to_node()])
        store.write_nodes([handler], file_id=py_fid)
        store.write_nodes([caller], file_id=ts_fid)
        store.write_edges([
            Edge(src=handler.id, dst=contract.id, kind=EdgeKind.EXPOSES,
                 evidence=Evidence.fact("decorator"), resolution=Resolution.EXACT,
                 provenance="fastapi.route"),
            Edge(src=caller.id, dst=contract.id, kind=EdgeKind.CONSUMES,
                 evidence=Evidence.heuristic("route_key_match"), resolution=Resolution.INFERRED,
                 provenance="js.fetch"),
        ])

        joined = store.edges_to(contract.id)
        assert {e.kind for e in joined} == {EdgeKind.EXPOSES, EdgeKind.CONSUMES}
        assert {e.src for e in joined} == {handler.id, caller.id}

    def test_consumer_without_exposer_is_visible(self, store):
        """A dangling contract is a finding, not a silent gap (§8.1)."""
        fid = store.write_file(make_file("web/api.ts", "typescript"))
        contract = Contract(id=contract_id("http_route", "GET /external/{*}"),
                            kind=ContractKind.HTTP_ROUTE, key="GET /external/{id}",
                            normalised_key="GET /external/{*}")
        caller = make_node("callOut", kind=NodeKind.FUNCTION, file_path="web/api.ts")
        store.write_nodes([contract.to_node()])
        store.write_nodes([caller], file_id=fid)
        store.write_edges([Edge(src=caller.id, dst=contract.id, kind=EdgeKind.CONSUMES,
                                evidence=Evidence.heuristic("route_key_match"),
                                resolution=Resolution.INFERRED, provenance="js.fetch")])

        edges = store.edges_to(contract.id)
        assert [e.kind for e in edges] == [EdgeKind.CONSUMES]
        assert not any(e.kind is EdgeKind.EXPOSES for e in edges)


# ---------------------------------------------------------------------------
# §11 / §16.24 -- determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def _build(self, path, node_order, edge_order):
        st = GraphStore.create(path, project_root="/p")
        fid = st.write_file(make_file())
        nodes = [make_node("a"), make_node("b", ["b"], kind=NodeKind.FUNCTION),
                 make_node("c", ["c"], kind=NodeKind.CLASS)]
        st.write_nodes([nodes[i] for i in node_order], file_id=fid)
        edges = [
            Edge(src=nodes[0].id, dst=nodes[1].id, kind=EdgeKind.CALLS,
                 evidence=Evidence.fact("ast"), resolution=Resolution.EXACT,
                 provenance="t", span=Span(10, 20, 5, 0, 5, 10)),
            Edge(src=nodes[0].id, dst=nodes[2].id, kind=EdgeKind.HAS_TYPE,
                 evidence=Evidence.fact("ast"), resolution=Resolution.EXACT,
                 provenance="t", span=Span(30, 40, 9, 0, 9, 10)),
        ]
        st.write_edges([edges[i] for i in edge_order], file_id=fid)
        digest = st.finalise(grammar_digest="g")
        st.close()
        return digest

    def test_digest_is_independent_of_insertion_order(self, tmp_path):
        """Order-dependence here would silently defeat ``--verify-determinism``."""
        forward = self._build(tmp_path / "a.sqlite", [0, 1, 2], [0, 1])
        shuffled = self._build(tmp_path / "b.sqlite", [2, 0, 1], [1, 0])
        assert forward == shuffled

    def test_digest_changes_when_content_changes(self, tmp_path):
        base = self._build(tmp_path / "a.sqlite", [0, 1, 2], [0, 1])
        st = GraphStore.create(tmp_path / "c.sqlite", project_root="/p")
        fid = st.write_file(make_file())
        st.write_nodes([make_node("a")], file_id=fid)
        changed = st.finalise(grammar_digest="g")
        st.close()
        assert base != changed


# ---------------------------------------------------------------------------
# §17.2 -- clean break
# ---------------------------------------------------------------------------


class TestSchemaRefusal:
    def test_missing_index_raises(self, tmp_path):
        with pytest.raises(GraphNotFound, match="No graph index"):
            GraphStore.open(tmp_path / "absent.sqlite")

    def test_older_schema_is_refused_not_migrated(self, tmp_path):
        """Silently tolerating an old store is how schema 3 served an empty graph."""
        path = tmp_path / "old.sqlite"
        st = GraphStore.create(path, project_root="/p")
        st.conn.execute("UPDATE graph_meta SET schema_version = 3")
        st.conn.commit()
        st.close()
        with pytest.raises(SchemaMismatch, match="clean break with no upgrade path"):
            GraphStore.open(path)

    def test_non_lineagelens_database_is_refused(self, tmp_path):
        path = tmp_path / "foreign.sqlite"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatch):
            GraphStore.open(path)

    def test_current_schema_opens(self, tmp_path):
        path = tmp_path / "ok.sqlite"
        GraphStore.create(path, project_root="/p").close()
        with GraphStore.open(path) as st:
            assert st.project_root == "/p"
            assert st.conn.execute(
                "SELECT schema_version FROM graph_meta"
            ).fetchone()[0] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# boundaries (§9.1)
# ---------------------------------------------------------------------------


class TestBoundaries:
    def test_boundary_retains_candidate_set(self, store):
        """An enumerated ambiguity beats both a guess and silence."""
        fid = store.write_file(make_file())
        a = make_node("submit")
        store.write_nodes([a], file_id=fid)
        store.write_boundaries([Boundary(
            node_id=a.id, kind=BoundaryKind.DYNAMIC_DISPATCH,
            detail="Repository#save has 4 implementations",
            candidates=("SqlRepo", "MemRepo", "CachedRepo", "AuditRepo"),
        )])

        got = store.boundaries_for_nodes([a.id])[0]
        assert got.kind is BoundaryKind.DYNAMIC_DISPATCH
        assert len(got.candidates) == 4
        assert "4 implementations" in got.detail
