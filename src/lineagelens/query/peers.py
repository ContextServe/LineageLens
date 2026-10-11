"""Peers that share a hub, and nothing those edges do not say.

A site and its peer both point *at* a hub. There is no edge from one site to
the other, so a forward walk stops on the hub. The two hubs this knows are a
declared type (``HAS_TYPE``) and a contract node (``EXPOSES`` / ``CONSUMES``).

What gets said:

* A shared declared type is not a call and not a send. The entry names the
  type and carries ``verification``, which tells the reader to check the code.
* A contract whose roles are exactly one ``EXPOSES`` and one ``CONSUMES`` has
  a direction the index recorded. The entry states those roles and the weaker
  evidence tier. It does not carry ``verification``.
* Any other contract role pair has no single direction. The entry states the
  roles that were stored and carries ``verification``.

``verification`` is omitted when the direction is recorded. It is not attached
to every peer.

A type referenced from more than :data:`MAX_SHARED_TYPE_SITES` sites is not a
peer list. Naming every co-user would read as a flow. The envelope records
``peer_fan_in`` and no peer is emitted for that type. Asking for the type
itself still uses the ordinary dependent walk; that question is "who references
this type", which the edges do answer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..core import Edge, EdgeKind, EvidenceTier, Node, NodeKind
from ..store import GraphStore
from .budget import Envelope

#: Above this many sites, a type is too widely shared to name co-users as
#: peers. The cap is a count of distinct sites, not a denylist of type names,
#: so it applies to whatever the index actually resolved.
MAX_SHARED_TYPE_SITES = 16

_CONTRACT_KINDS = frozenset({EdgeKind.EXPOSES, EdgeKind.CONSUMES})
_TIER_RANK = {
    EvidenceTier.FACT: 2,
    EvidenceTier.HEURISTIC: 1,
    EvidenceTier.PROBABILISTIC: 0,
}

TYPE_VERIFICATION = (
    "Both sites reference this type. The index has no edge that says one "
    "calls or sends to the other. Read the code at both sites before "
    "treating this as a flow."
)

CONTRACT_VERIFICATION = (
    "The index does not record a single direction between these sites. "
    "Read the code before treating one as the sender or the receiver."
)


@dataclass(frozen=True, slots=True)
class PeerLink:
    """One other site that points at the same hub."""

    relation: str
    site: Node
    peer: Node
    hub: Node
    site_kinds: frozenset[EdgeKind]
    peer_kinds: frozenset[EdgeKind]
    site_edges: tuple[Edge, ...]
    peer_edges: tuple[Edge, ...]
    service_relation: str

    @property
    def flow_ambiguous(self) -> bool:
        """True when the edges do not record a single direction.

        A shared type never does. A contract does only when one side exposes
        and the other consumes, and neither side does both.
        """
        if self.relation == "shares_type":
            return True
        one_each = (
            (frozenset({EdgeKind.EXPOSES}), frozenset({EdgeKind.CONSUMES})),
            (frozenset({EdgeKind.CONSUMES}), frozenset({EdgeKind.EXPOSES})),
        )
        return (self.site_kinds, self.peer_kinds) not in one_each

    @property
    def evidence_tier(self) -> str:
        """The weaker tier on the edges that form this link.

        A fact must not be reported when the other edge is a heuristic.
        """
        edges = (*self.site_edges, *self.peer_edges)
        weakest = min(edges, key=lambda edge: _TIER_RANK[edge.evidence.tier])
        return weakest.evidence.tier.value

    def as_dict(self) -> dict[str, object]:
        """The impact / search shape. ``verification`` only when ambiguous."""
        out: dict[str, object] = {
            "relation": self.relation,
            "node": self.peer.qualified_name,
            "at": _at(self.peer),
            "hub": self.hub.qualified_name,
            "hub_key": self.hub.signature or self.hub.name,
            "hub_kind": _hub_kind(self.hub, self.site_edges),
            "self_role": _role_names(self.site_kinds),
            "peer_role": _role_names(self.peer_kinds),
            "evidence_tier": self.evidence_tier,
            "service_relation": self.service_relation,
            "distance": 2,
        }
        _attach_verification(out, self)
        return out

    def as_explain(self) -> dict[str, object]:
        """A path through the hub, drawn with both edges pointing inward."""
        site_role = ",".join(_role_names(self.site_kinds))
        peer_role = ",".join(_role_names(self.peer_kinds))
        out: dict[str, object] = {
            "kind": self.relation,
            "evidence_tier": self.evidence_tier,
            "evidence": sorted({
                edge.evidence.label for edge in (*self.site_edges, *self.peer_edges)
            }),
            "provenance": sorted({
                edge.provenance for edge in (*self.site_edges, *self.peer_edges)
            }),
            "hub": self.hub.qualified_name,
            "hub_key": self.hub.signature or self.hub.name,
            "hub_kind": _hub_kind(self.hub, self.site_edges),
            "self_role": _role_names(self.site_kinds),
            "peer_role": _role_names(self.peer_kinds),
            "service_relation": self.service_relation,
            "path": (
                f"{self.site.name} -{site_role}-> {self.hub.name} "
                f"<-{peer_role}- {self.peer.name}"
            ),
            "written_at": [
                _written(edge) for edge in (*self.site_edges, *self.peer_edges)
            ],
        }
        _attach_verification(out, self)
        return out

    def as_path(self, sources: dict[str, str] | None = None) -> dict[str, object]:
        """A find_paths result that is a hub, not a forward chain.

        The last hop is the peer's own edge into the hub, so ``reversed`` is
        set. The kind string stays the stored edge kind.
        """
        sources = sources or {}
        site_edge = self.site_edges[0]
        peer_edge = self.peer_edges[0]
        out: dict[str, object] = {
            "direction": "shared_hub",
            "relation": self.relation,
            "length": 2,
            "chain": f"{self.site.name} -> {self.hub.name} -> {self.peer.name}",
            "hops": [
                _hop(self.site, None, 0, sources),
                _hop(self.hub, site_edge, 1, sources, reversed_=False),
                _hop(self.peer, peer_edge, 2, sources, reversed_=True),
            ],
            "hub": self.hub.qualified_name,
            "hub_kind": _hub_kind(self.hub, self.site_edges),
            "self_role": _role_names(self.site_kinds),
            "peer_role": _role_names(self.peer_kinds),
            "evidence_tier": self.evidence_tier,
            "service_relation": self.service_relation,
        }
        _attach_verification(out, self)
        return out


def shared_hubs(
    store: GraphStore,
    site_id: str,
    *,
    peer_id: str | None = None,
    include_types: bool = True,
    include_contracts: bool = True,
    limit: int,
    envelope: Envelope | None = None,
) -> list[PeerLink]:
    """Other sites that share a type or a contract hub with ``site_id``.

    ``peer_id`` restricts the result to one other site, for explain and for
    find_paths. Results are ordered by relation, hub, then peer, so two indexes
    of one repository return the same list.
    """
    site = store.get_node(site_id)
    if site is None or limit <= 0:
        return []

    links: list[PeerLink] = []
    if include_types:
        links.extend(_type_links(store, site, envelope))
    if include_contracts:
        links.extend(_contract_links(store, site))

    if peer_id is not None:
        links = [link for link in links if link.peer.id == peer_id]

    links.sort(key=lambda link: (
        link.relation,
        link.hub.qualified_name,
        link.peer.qualified_name,
        tuple(sorted(kind.value for kind in link.peer_kinds)),
    ))
    if len(links) > limit:
        if envelope is not None:
            envelope.add_boundary(
                "peer_limit",
                node_id=site_id,
                detail=(
                    f"{len(links)} shared-hub peers; returning {limit}"
                ),
            )
        links = links[:limit]
    return links


def _type_links(
    store: GraphStore, site: Node, envelope: Envelope | None
) -> list[PeerLink]:
    by_hub: dict[str, list[Edge]] = defaultdict(list)
    for edge in store.edges_from(site.id, [EdgeKind.HAS_TYPE]):
        by_hub[edge.dst].append(edge)

    links: list[PeerLink] = []
    for hub_id, site_edges in by_hub.items():
        hub = store.get_node(hub_id)
        if hub is None or not hub.is_type:
            continue
        incoming = store.edges_to(hub.id, [EdgeKind.HAS_TYPE])
        others = {edge.src for edge in incoming if edge.src != site.id}
        if not others:
            continue
        if not hub.file_path:
            if envelope is not None:
                envelope.add_boundary(
                    "peer_undeclared_type",
                    node_id=hub.id,
                    detail=(
                        f"{hub.qualified_name} has no declaration file; "
                        "no peer is named"
                    ),
                )
            continue
        sites = others | {site.id}
        if len(sites) > MAX_SHARED_TYPE_SITES:
            if envelope is not None:
                envelope.add_boundary(
                    "peer_fan_in",
                    node_id=hub.id,
                    detail=(
                        f"{hub.qualified_name} is referenced by {len(sites)} "
                        "sites; no peer is named"
                    ),
                )
            continue
        grouped = _group_by_src(incoming, exclude=site.id)
        for peer_id, peer_edges in grouped.items():
            peer = store.get_node(peer_id)
            if peer is None:
                continue
            links.append(_link(
                "shares_type", site, peer, hub, site_edges, peer_edges
            ))
    return links


def _contract_links(store: GraphStore, site: Node) -> list[PeerLink]:
    by_hub: dict[str, list[Edge]] = defaultdict(list)
    for edge in store.edges_from(site.id, _CONTRACT_KINDS):
        by_hub[edge.dst].append(edge)

    links: list[PeerLink] = []
    for hub_id, site_edges in by_hub.items():
        hub = store.get_node(hub_id)
        if hub is None or hub.kind is not NodeKind.CONTRACT:
            continue
        incoming = store.edges_to(hub.id, _CONTRACT_KINDS)
        grouped = _group_by_src(incoming, exclude=site.id)
        for peer_id, peer_edges in grouped.items():
            peer = store.get_node(peer_id)
            if peer is None:
                continue
            links.append(_link(
                "shares_contract", site, peer, hub, site_edges, peer_edges
            ))
    return links


def _link(
    relation: str,
    site: Node,
    peer: Node,
    hub: Node,
    site_edges: list[Edge],
    peer_edges: list[Edge],
) -> PeerLink:
    site_edges = sorted(site_edges, key=lambda edge: edge.identity)
    peer_edges = sorted(peer_edges, key=lambda edge: edge.identity)
    return PeerLink(
        relation=relation,
        site=site,
        peer=peer,
        hub=hub,
        site_kinds=frozenset(edge.kind for edge in site_edges),
        peer_kinds=frozenset(edge.kind for edge in peer_edges),
        site_edges=tuple(site_edges),
        peer_edges=tuple(peer_edges),
        service_relation=_service_relation(site, peer),
    )


def _group_by_src(
    edges: list[Edge], *, exclude: str
) -> dict[str, list[Edge]]:
    grouped: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        if edge.src == exclude:
            continue
        grouped[edge.src].append(edge)
    return grouped


def _service_relation(site: Node, peer: Node) -> str:
    if not site.service_id or not peer.service_id:
        return "unknown"
    if site.service_id == peer.service_id:
        return "same_service"
    return "cross_service"


def _hub_kind(hub: Node, site_edges: list[Edge] | tuple[Edge, ...]) -> str:
    """The stored contract kind, or the type node's own kind.

    Contract kinds stay the ones the adapter wrote (``topic``, ``http_route``,
    and the rest). No extra kind is introduced for the hop.
    """
    for edge in site_edges:
        kind = edge.metadata.get("contract_kind")
        if kind:
            return str(kind)
    return hub.kind.value


def _role_names(kinds: frozenset[EdgeKind]) -> list[str]:
    return sorted(kind.value for kind in kinds)


def _attach_verification(out: dict[str, object], link: PeerLink) -> None:
    if not link.flow_ambiguous:
        return
    out["verification"] = (
        TYPE_VERIFICATION if link.relation == "shares_type" else CONTRACT_VERIFICATION
    )


def _at(node: Node) -> str:
    return f"{node.file_path}:{node.span.start_line}"


def _written(edge: Edge) -> str:
    if edge.span is not None and edge.file_path:
        return f"{edge.file_path}:{edge.span.start_line}"
    return edge.file_path or ""


def _hop(
    node: Node,
    edge: Edge | None,
    depth: int,
    sources: dict[str, str],
    reversed_: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "depth": depth,
        "node": node.qualified_name,
        "kind": node.kind.value,
        "at": _at(node),
    }
    if edge is not None:
        payload["via"] = edge.kind.value
        payload["evidence"] = edge.evidence.tier.value
        if reversed_:
            payload["reversed"] = True
        if edge.span is not None and edge.file_path:
            payload["written_at"] = f"{edge.file_path}:{edge.span.start_line}"
    source = sources.get(node.id)
    if source is not None:
        payload["source"] = source
    return payload
