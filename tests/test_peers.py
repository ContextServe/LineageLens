"""Shared-hub peers.

The hop is the graph shape "two sites point at one hub". It is not a detector
for one framework. A result states only the edges that were stored, and it
carries ``verification`` only when those edges do not give a direction.
"""

from __future__ import annotations

import pytest

from lineagelens.core import (
    Edge,
    EdgeKind,
    Evidence,
    EvidenceTier,
    Node,
    NodeKind,
    Resolution,
    Service,
    Span,
)
from lineagelens.query import QueryEngine
from lineagelens.query.peers import (
    CONTRACT_VERIFICATION,
    MAX_SHARED_TYPE_SITES,
    TYPE_VERIFICATION,
)
from lineagelens.store import GraphStore

SVC_A = "svc-a"
SVC_B = "svc-b"


def _node(
    name: str,
    kind: NodeKind,
    *,
    service: str | None = SVC_A,
    file_path: str = "src/mod.py",
    line: int = 1,
    signature: str | None = None,
) -> Node:
    return Node(
        id=name,
        kind=kind,
        name=name.rsplit("/", 1)[-1],
        qualified_name=name,
        lang="python",
        span=Span.at_line(line),
        file_path=file_path,
        service_id=service,
        signature=signature,
    )


def _edge(
    src: Node,
    dst: Node,
    kind: EdgeKind,
    *,
    line: int,
    tier: EvidenceTier = EvidenceTier.FACT,
    label: str = "test",
    metadata: dict | None = None,
) -> Edge:
    return Edge(
        src=src.id,
        dst=dst.id,
        kind=kind,
        evidence=Evidence(tier=tier, label=label),
        resolution=Resolution.EXACT,
        provenance=label,
        span=Span.at_line(line),
        file_path=src.file_path or "src/mod.py",
        metadata=metadata or {},
    )


def _engine(tmp_path, nodes: list[Node], edges: list[Edge]) -> QueryEngine:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = GraphStore.create(tmp_path / "graph.sqlite", project_root=str(tmp_path))
    store.write_services([
        Service(id=SVC_A, name="a", root=".", kind="pypi"),
        Service(id=SVC_B, name="b", root="web", kind="npm"),
    ])
    store.write_nodes(nodes)
    store.write_edges(edges)
    return QueryEngine(store, str(tmp_path))


def _peer(report: dict, name: str) -> dict:
    matches = [entry for entry in report.get("peers", []) if entry["node"] == name]
    assert matches, report.get("peers")
    return matches[0]


class TestSharedType:
    def test_a_declared_type_names_the_other_site_and_asks_for_code(self, tmp_path):
        """Enum, struct, and class are the same hop. The entry is not a send."""
        for kind in (NodeKind.ENUM, NodeKind.STRUCT, NodeKind.CLASS):
            command = _node("app/Command", kind, line=1, file_path="src/command.py")
            send = _node("app/send", NodeKind.FUNCTION, line=10)
            recv = _node("app/recv", NodeKind.FUNCTION, line=20)
            engine = _engine(tmp_path / kind.value, [command, send, recv], [
                _edge(send, command, EdgeKind.HAS_TYPE, line=11),
                _edge(recv, command, EdgeKind.HAS_TYPE, line=21),
            ])

            report = engine.impact_of(send.id).results[0].as_dict()
            peer = _peer(report, recv.qualified_name)
            assert peer["relation"] == "shares_type"
            assert peer["hub_kind"] == kind.value
            assert peer["self_role"] == ["HAS_TYPE"]
            assert peer["peer_role"] == ["HAS_TYPE"]
            assert peer["service_relation"] == "same_service"
            assert peer["verification"] == TYPE_VERIFICATION
            assert recv.qualified_name not in {
                entry["node"] for entry in report.get("in_process", [])
            }
            assert "via_contract" not in peer

    def test_fan_in_names_no_peer(self, tmp_path):
        """A widely shared type is a boundary, not a list of flows."""
        shared = _node("app/Wide", NodeKind.CLASS, line=1, file_path="src/wide.py")
        sites = [
            _node(f"app/use{i}", NodeKind.FUNCTION, line=10 + i)
            for i in range(MAX_SHARED_TYPE_SITES + 1)
        ]
        rare = _node("app/Rare", NodeKind.STRUCT, line=2, file_path="src/rare.py")
        other = _node("app/other", NodeKind.FUNCTION, line=80)
        edges = [
            _edge(site, shared, EdgeKind.HAS_TYPE, line=100 + i)
            for i, site in enumerate(sites)
        ]
        edges.append(_edge(sites[0], rare, EdgeKind.HAS_TYPE, line=200))
        edges.append(_edge(other, rare, EdgeKind.HAS_TYPE, line=201))
        engine = _engine(tmp_path, [shared, rare, other, *sites], edges)

        result = engine.impact_of(sites[0].id)
        report = result.results[0].as_dict()
        names = {entry["node"] for entry in report.get("peers", [])}
        assert other.qualified_name in names
        assert all(not name.startswith("app/use") for name in names)
        assert "peer_fan_in" in result.envelope.boundaries
        detail = " ".join(
            item["detail"] for item in result.envelope.boundary_detail
        )
        assert "no peer is named" in detail

    def test_a_type_with_no_declaration_file_names_no_peer(self, tmp_path):
        phantom = _node("app/Phantom", NodeKind.CLASS, line=1, file_path="")
        send = _node("app/send", NodeKind.FUNCTION, line=4)
        recv = _node("app/recv", NodeKind.FUNCTION, line=8)
        engine = _engine(tmp_path, [phantom, send, recv], [
            _edge(send, phantom, EdgeKind.HAS_TYPE, line=5),
            _edge(recv, phantom, EdgeKind.HAS_TYPE, line=9),
        ])
        result = engine.impact_of(send.id)
        assert result.results[0].as_dict().get("peers", []) == []
        assert "peer_undeclared_type" in result.envelope.boundaries

    def test_impact_of_the_type_still_lists_its_references(self, tmp_path):
        """The fan-in cap applies to the sibling hop, not to 'who uses this type'."""
        shared = _node("app/Wide", NodeKind.CLASS, line=1, file_path="src/wide.py")
        sites = [
            _node(f"app/use{i}", NodeKind.FUNCTION, line=10 + i)
            for i in range(MAX_SHARED_TYPE_SITES + 1)
        ]
        edges = [
            _edge(site, shared, EdgeKind.HAS_TYPE, line=100 + i)
            for i, site in enumerate(sites)
        ]
        engine = _engine(tmp_path, [shared, *sites], edges)
        report = engine.impact_of(shared.id, limit=50).results[0].as_dict()
        reached = {entry["node"] for entry in report.get("in_process", [])}
        assert {site.qualified_name for site in sites} <= reached


class TestSharedContract:
    @pytest.mark.parametrize("contract_kind", ["topic", "http_route"])
    def test_complementary_roles_state_the_direction(self, tmp_path, contract_kind):
        hub = _node(
            f"contract/{contract_kind}/orders",
            NodeKind.CONTRACT,
            service=None,
            file_path="",
            signature="orders",
        )
        exposer = _node("app/publish", NodeKind.FUNCTION, service=SVC_A, line=3)
        consumer = _node(
            "web/subscribe", NodeKind.FUNCTION, service=SVC_B, line=9,
            file_path="web/client.ts",
        )
        meta = {"contract_kind": contract_kind, "contract_key": "orders"}
        engine = _engine(tmp_path / contract_kind, [hub, exposer, consumer], [
            _edge(
                exposer, hub, EdgeKind.EXPOSES, line=4,
                tier=EvidenceTier.HEURISTIC, label="adapter", metadata=meta,
            ),
            _edge(
                consumer, hub, EdgeKind.CONSUMES, line=10,
                tier=EvidenceTier.FACT, label="registry", metadata=meta,
            ),
        ])

        report = engine.impact_of(exposer.id).results[0].as_dict()
        peer = _peer(report, consumer.qualified_name)
        assert peer["relation"] == "shares_contract"
        assert peer["hub_kind"] == contract_kind
        assert peer["self_role"] == ["EXPOSES"]
        assert peer["peer_role"] == ["CONSUMES"]
        assert peer["evidence_tier"] == "heuristic"
        assert peer["service_relation"] == "cross_service"
        assert "verification" not in peer
        assert any(
            entry["node"] == consumer.qualified_name and "via_contract" in entry
            for entry in report["cross_service"]
        )

        from_consumer = engine.impact_of(consumer.id).results[0].as_dict()
        reverse = _peer(from_consumer, exposer.qualified_name)
        assert reverse["peer_role"] == ["EXPOSES"]
        assert "verification" not in reverse
        assert exposer.qualified_name not in {
            entry["node"] for entry in from_consumer.get("cross_service", [])
        }

    def test_same_service_is_not_called_cross_service(self, tmp_path):
        hub = _node(
            "contract/topic/orders", NodeKind.CONTRACT,
            service=None, file_path="", signature="orders",
        )
        exposer = _node("app/publish", NodeKind.FUNCTION, service=SVC_A, line=3)
        consumer = _node("app/handle", NodeKind.FUNCTION, service=SVC_A, line=12)
        meta = {"contract_kind": "topic", "contract_key": "orders"}
        engine = _engine(tmp_path, [hub, exposer, consumer], [
            _edge(exposer, hub, EdgeKind.EXPOSES, line=4, metadata=meta),
            _edge(consumer, hub, EdgeKind.CONSUMES, line=13, metadata=meta),
        ])
        report = engine.impact_of(exposer.id).results[0].as_dict()
        peer = _peer(report, consumer.qualified_name)
        assert peer["service_relation"] == "same_service"
        assert "verification" not in peer
        assert consumer.qualified_name not in {
            entry["node"] for entry in report.get("cross_service", [])
        }

    def test_the_same_role_on_both_sides_asks_for_code(self, tmp_path):
        hub = _node(
            "contract/topic/orders", NodeKind.CONTRACT,
            service=None, file_path="", signature="orders",
        )
        left = _node("app/publish", NodeKind.FUNCTION, service=SVC_A, line=3)
        right = _node("web/also", NodeKind.FUNCTION, service=SVC_B, line=8)
        meta = {"contract_kind": "topic"}
        engine = _engine(tmp_path, [hub, left, right], [
            _edge(left, hub, EdgeKind.EXPOSES, line=4, metadata=meta),
            _edge(right, hub, EdgeKind.EXPOSES, line=9, metadata=meta),
        ])
        report = engine.impact_of(left.id).results[0].as_dict()
        peer = _peer(report, right.qualified_name)
        assert peer["self_role"] == ["EXPOSES"]
        assert peer["peer_role"] == ["EXPOSES"]
        assert peer["verification"] == CONTRACT_VERIFICATION
        assert right.qualified_name not in {
            entry["node"] for entry in report.get("cross_service", [])
        }

    def test_a_missing_service_is_unknown(self, tmp_path):
        hub = _node(
            "contract/topic/orders", NodeKind.CONTRACT,
            service=None, file_path="", signature="orders",
        )
        exposer = _node("app/publish", NodeKind.FUNCTION, service=None, line=3)
        consumer = _node("app/handle", NodeKind.FUNCTION, service=SVC_A, line=8)
        meta = {"contract_kind": "topic"}
        engine = _engine(tmp_path, [hub, exposer, consumer], [
            _edge(exposer, hub, EdgeKind.EXPOSES, line=4, metadata=meta),
            _edge(consumer, hub, EdgeKind.CONSUMES, line=9, metadata=meta),
        ])
        peer = _peer(
            engine.impact_of(exposer.id).results[0].as_dict(),
            consumer.qualified_name,
        )
        assert peer["service_relation"] == "unknown"
        assert "verification" not in peer


class TestExplainAndPaths:
    def test_explain_draws_the_type_inward_and_asks_for_code(self, tmp_path):
        command = _node("app/Command", NodeKind.ENUM, line=1, file_path="src/command.py")
        send = _node("app/send", NodeKind.FUNCTION, line=10)
        recv = _node("app/recv", NodeKind.FUNCTION, line=20)
        engine = _engine(tmp_path, [command, send, recv], [
            _edge(send, command, EdgeKind.HAS_TYPE, line=11),
            _edge(recv, command, EdgeKind.HAS_TYPE, line=21),
        ])
        result = engine.explain(send.id, recv.id)
        assert result.results
        entry = result.results[0]
        assert entry["kind"] == "shares_type"
        assert entry["path"] == "send -HAS_TYPE-> Command <-HAS_TYPE- recv"
        assert entry["verification"] == TYPE_VERIFICATION

    def test_a_direct_edge_is_the_explanation(self, tmp_path):
        send = _node("app/send", NodeKind.FUNCTION, line=10)
        recv = _node("app/recv", NodeKind.FUNCTION, line=20)
        command = _node("app/Command", NodeKind.ENUM, line=1, file_path="src/command.py")
        engine = _engine(tmp_path, [send, recv, command], [
            _edge(send, recv, EdgeKind.CALLS, line=12, label="call"),
            _edge(send, command, EdgeKind.HAS_TYPE, line=11),
            _edge(recv, command, EdgeKind.HAS_TYPE, line=21),
        ])
        result = engine.explain(send.id, recv.id)
        assert [entry["kind"] for entry in result.results] == ["CALLS"]
        assert "verification" not in result.results[0]

    def test_find_paths_keeps_a_call_chain_ahead_of_a_hub(self, tmp_path):
        send = _node("app/send", NodeKind.FUNCTION, line=10)
        recv = _node("app/recv", NodeKind.FUNCTION, line=20)
        command = _node("app/Command", NodeKind.ENUM, line=1, file_path="src/command.py")
        engine = _engine(tmp_path, [send, recv, command], [
            _edge(send, recv, EdgeKind.CALLS, line=12),
            _edge(send, command, EdgeKind.HAS_TYPE, line=11),
            _edge(recv, command, EdgeKind.HAS_TYPE, line=21),
        ])
        result = engine.find_paths(send.id, recv.id)
        assert result.ranking == "shortest_path_first"
        assert result.results[0]["direction"] == "forward"
        assert all(item.get("relation") != "shares_type" for item in result.results)

    def test_find_paths_reports_a_type_hub_only_when_nothing_else_connects(
        self, tmp_path
    ):
        command = _node("app/Command", NodeKind.ENUM, line=1, file_path="src/command.py")
        send = _node("app/send", NodeKind.FUNCTION, line=10)
        recv = _node("app/recv", NodeKind.FUNCTION, line=20)
        engine = _engine(tmp_path, [command, send, recv], [
            _edge(send, command, EdgeKind.HAS_TYPE, line=11),
            _edge(recv, command, EdgeKind.HAS_TYPE, line=21),
        ])
        result = engine.find_paths(send.id, recv.id)
        assert result.ranking == "shared_hub"
        entry = result.results[0]
        assert entry["relation"] == "shares_type"
        assert entry["hops"][2]["reversed"] is True
        assert entry["hops"][2]["via"] == "HAS_TYPE"
        assert entry["verification"] == TYPE_VERIFICATION

        calls_only = engine.find_paths(send.id, recv.id, kinds=["CALLS"])
        assert calls_only.results == []
        assert calls_only.ranking == "shortest_path_first"

    def test_find_paths_states_a_recorded_contract_direction(self, tmp_path):
        hub = _node(
            "contract/http_route/orders", NodeKind.CONTRACT,
            service=None, file_path="", signature="/orders",
        )
        exposer = _node("app/publish", NodeKind.FUNCTION, service=SVC_A, line=3)
        consumer = _node("web/subscribe", NodeKind.FUNCTION, service=SVC_B, line=9)
        meta = {"contract_kind": "http_route"}
        engine = _engine(tmp_path, [hub, exposer, consumer], [
            _edge(exposer, hub, EdgeKind.EXPOSES, line=4, metadata=meta),
            _edge(consumer, hub, EdgeKind.CONSUMES, line=10, metadata=meta),
        ])
        result = engine.find_paths(exposer.id, consumer.id)
        assert result.ranking == "shared_hub"
        entry = result.results[0]
        assert entry["relation"] == "shares_contract"
        assert entry["hub_kind"] == "http_route"
        assert entry["self_role"] == ["EXPOSES"]
        assert entry["peer_role"] == ["CONSUMES"]
        assert "verification" not in entry

    def test_search_does_not_attach_peers(self, tmp_path):
        command = _node("app/Command", NodeKind.ENUM, line=1, file_path="src/command.py")
        send = _node("app/send", NodeKind.FUNCTION, line=10)
        recv = _node("app/recv", NodeKind.FUNCTION, line=20)
        engine = _engine(tmp_path, [command, send, recv], [
            _edge(send, command, EdgeKind.HAS_TYPE, line=11),
            _edge(recv, command, EdgeKind.HAS_TYPE, line=21),
        ])
        result = engine.search("send")
        assert result.results
        assert "peers" not in result.results[0]
        assert "verification" not in result.results[0]
