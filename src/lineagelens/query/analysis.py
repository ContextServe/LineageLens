"""Impact, data flow, contracts, motifs, stack traces (§10.3, §10.4).

The two headline capabilities live here.

``impact_of(file:line)`` is the requirement that forced spans onto edges. A line
maps to the *operations written on it* -- not merely to its enclosing symbol --
so the answer can distinguish "line 412 calls save()" from "line 412 is
somewhere inside submit()". No statement-level nodes were needed for that; edges
carry spans instead, which keeps the graph at symbol granularity.

``dataflow_of`` walks READS/WRITES/PARAM_BINDS/RETURNS, so "where does this
value come from and where does it end up" crosses call boundaries. Where a
chain provably cannot continue it reports a boundary instead of guessing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..core import DATA_EDGES, EdgeKind, Intent, Node, NodeFlags
from ..store import GraphStore
from .budget import Budget, Envelope
from .traverse import Traverser

#: Edge kinds that mean "something depends on this symbol". A change to the
#: symbol can break any of them.
DEPENDENT_EDGES = (
    EdgeKind.CALLS, EdgeKind.REFERENCES, EdgeKind.OVERRIDES,
    EdgeKind.IMPLEMENTS, EdgeKind.INHERITS, EdgeKind.HAS_TYPE,
    EdgeKind.INSTANTIATES, EdgeKind.DECORATES, EdgeKind.IMPORTS,
)


class ChangeKind(str):
    """What kind of edit a line represents.

    The distinction matters because the blast radii differ by orders of
    magnitude: a body change is local, a signature change breaks every caller,
    and a string literal that happens to be a route key is a cross-service wire
    change. Schema 3 could not tell them apart, so the third was invisible.
    """

    SIGNATURE = "signature"
    BODY = "body"
    CONTRACT_KEY = "contract_key"
    TYPE = "type"
    ANNOTATION = "annotation"
    VISIBILITY = "visibility"
    DECLARATION = "declaration"


@dataclass(slots=True)
class ImpactReport:
    """A typed blast radius."""

    target: str
    change_kind: str
    at: str = ""
    anchored_operations: list[dict[str, Any]] = field(default_factory=list)
    in_process: list[dict[str, Any]] = field(default_factory=list)
    cross_service: list[dict[str, Any]] = field(default_factory=list)
    data: list[dict[str, Any]] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "target": self.target,
            "change_kind": self.change_kind,
        }
        if self.at:
            out["at"] = self.at
        for name, value in (
            ("anchored_operations", self.anchored_operations),
            ("in_process", self.in_process),
            ("cross_service", self.cross_service),
            ("data", self.data),
            ("entry_points", self.entry_points),
        ):
            if value:
                out[name] = value
        if self.source:
            out["source"] = self.source
        return out


class Analyser:
    """Impact, data flow, contracts, motifs and stack traces over one store."""

    def __init__(self, store: GraphStore, project_root: str) -> None:
        self.store = store
        self.project_root = project_root
        self.traverser = Traverser(store)

    # ---- §10.4 impact -----------------------------------------------------

    def impact_of_line(
        self,
        file_path: str,
        line: int,
        *,
        intent: Intent,
        budget: Budget,
        envelope: Envelope,
    ) -> ImpactReport | None:
        """What does changing ``file_path:line`` affect?"""
        containing = self.store.nodes_containing_line(file_path, line)
        if not containing:
            return None

        target = containing[-1]  # innermost: the thing being edited
        anchored = self.store.edges_on_line(file_path, line)
        change_kind = self._classify(target, anchored, line, containing)

        report = ImpactReport(
            target=target.qualified_name,
            change_kind=change_kind,
            at=f"{file_path}:{line}",
            anchored_operations=[
                {
                    "kind": edge.kind.value,
                    "target": (
                        node.qualified_name
                        if (node := self.store.get_node(edge.dst))
                        else edge.dst
                    ),
                    "evidence": edge.evidence.tier.value,
                }
                for edge in anchored
            ],
        )
        self._fill_impact(report, target, intent=intent, budget=budget,
                          envelope=envelope)

        if budget.wants_source(intent):
            from .traverse import source_for

            report.source = source_for(
                self.store, [target], self.project_root
            ).get(target.id, "")
        return report

    def impact_of_node(
        self,
        node: Node,
        *,
        intent: Intent,
        budget: Budget,
        envelope: Envelope,
    ) -> ImpactReport:
        report = ImpactReport(
            target=node.qualified_name,
            change_kind=ChangeKind.DECLARATION,
            at=f"{node.file_path}:{node.span.start_line}",
        )
        self._fill_impact(report, node, intent=intent, budget=budget,
                          envelope=envelope)
        return report

    def _fill_impact(
        self,
        report: ImpactReport,
        target: Node,
        *,
        intent: Intent,
        budget: Budget,
        envelope: Envelope,
    ) -> None:
        """Populate in-process, cross-service and data impact."""
        kinds = [str(k) for k in DEPENDENT_EDGES]
        dependents = self.traverser.walk(
            target.id, forward=False, kinds=kinds, budget=budget, envelope=envelope
        )

        own_service = target.service_id
        for path in dependents:
            node = path.hops[-1].node
            entry = {
                "node": node.qualified_name,
                "distance": path.length,
                "via": path.hops[-1].edge.kind.value if path.hops[-1].edge else "",
                "at": f"{node.file_path}:{node.span.start_line}",
            }
            # The distinction schema 3 could not make: a caller in the same
            # deployable is a build failure, whereas one in another service is
            # a wire-compatibility and deploy-ordering problem.
            if node.service_id and own_service and node.service_id != own_service:
                report.cross_service.append(entry)
            else:
                report.in_process.append(entry)
            if node.has(NodeFlags.ENTRY_POINT):
                report.entry_points.append(node.qualified_name)

        report.cross_service.extend(self._contract_consumers(target, own_service))

        if intent is Intent.PRECISE:
            report.data = self._data_impact(target, budget=budget, envelope=envelope)
        else:
            # `plan` skips data flow entirely -- roughly an order of magnitude
            # cheaper, and the shape of the dependency is what a survey needs.
            envelope.dataflow = {"mode": "skipped", "reason": "intent=plan"}

    def _contract_consumers(
        self, target: Node, own_service: str | None
    ) -> list[dict[str, Any]]:
        """Other services reaching this symbol through a contract (§8).

        A route handler has no in-repo caller at all; its consumers are on the
        far side of a contract node, and without this they are invisible.
        """
        out: list[dict[str, Any]] = []
        for exposes in self.store.edges_from(target.id, [EdgeKind.EXPOSES]):
            contract = self.store.get_node(exposes.dst)
            if contract is None:
                continue
            for consumes in self.store.edges_to(exposes.dst, [EdgeKind.CONSUMES]):
                consumer = self.store.get_node(consumes.src)
                if consumer is None or consumer.id == target.id:
                    continue
                if consumer.service_id == own_service:
                    continue
                out.append({
                    "node": consumer.qualified_name,
                    "via_contract": contract.signature or contract.name,
                    "contract_kind": exposes.metadata.get("contract_kind", ""),
                    "evidence": consumes.evidence.tier.value,
                    "at": f"{consumer.file_path}:{consumer.span.start_line}",
                })
        return out

    def _data_impact(
        self, target: Node, *, budget: Budget, envelope: Envelope
    ) -> list[dict[str, Any]]:
        kinds = [str(k) for k in DATA_EDGES if k is not EdgeKind.FLOWS_TO]
        out: list[dict[str, Any]] = []
        for direction, forward in (("downstream", True), ("upstream", False)):
            for path in self.traverser.walk(
                target.id, forward=forward, kinds=kinds,
                budget=budget, envelope=envelope,
            ):
                node = path.hops[-1].node
                out.append({
                    "direction": direction,
                    "node": node.qualified_name,
                    "kind": node.kind.value,
                    "distance": path.length,
                    "chain": path.as_dict()["chain"],
                })
        return out

    def _classify(
        self,
        target: Node,
        anchored: Sequence,
        line: int,
        containing: Sequence[Node] = (),
    ) -> str:
        """Classify the edit a line represents."""
        if any(
            e.kind in (EdgeKind.EXPOSES, EdgeKind.CONSUMES) for e in anchored
        ):
            return ChangeKind.CONTRACT_KEY
        if any(e.kind is EdgeKind.DECORATES for e in anchored):
            return ChangeKind.ANNOTATION

        # A callable's declaration line is its signature, and editing it
        # changes the contract with every caller -- a different radius from
        # editing a statement in the body.
        #
        # Checked across every containing node, not just the innermost. The
        # innermost node on a `def save(self, amount)` line is a *parameter*,
        # so looking only at the target would classify a signature edit as a
        # declaration and understate the blast radius.
        for node in containing or (target,):
            if node.is_callable and line == node.span.start_line:
                return ChangeKind.SIGNATURE

        if any(e.kind is EdgeKind.HAS_TYPE for e in anchored):
            return ChangeKind.TYPE
        if target.is_value:
            return ChangeKind.DECLARATION
        return ChangeKind.BODY

    # ---- §9 data flow -----------------------------------------------------

    def dataflow_of(
        self,
        node: Node,
        *,
        direction: str,
        budget: Budget,
        envelope: Envelope,
    ) -> list[dict[str, Any]]:
        """Where a value comes from and where it goes.

        Chains READS, WRITES, PARAM_BINDS and RETURNS, so the walk crosses call
        boundaries rather than stopping at them. FLOWS_TO is this closure --
        computed here rather than materialised, which keeps the store minimal
        and incremental updates cheap.
        """
        kinds = [str(k) for k in DATA_EDGES if k is not EdgeKind.FLOWS_TO]
        self._note_dataflow_state(node, envelope)

        flows: list[dict[str, Any]] = []
        wanted = (
            [("downstream", True), ("upstream", False)]
            if direction == "both"
            else [(direction, direction == "downstream")]
        )
        for label, forward in wanted:
            for path in self.traverser.walk(
                node.id, forward=forward, kinds=kinds,
                budget=budget, envelope=envelope,
            ):
                tail = path.hops[-1]
                flows.append({
                    "direction": label,
                    "node": tail.node.qualified_name,
                    "kind": tail.node.kind.value,
                    "distance": path.length,
                    "chain": path.as_dict()["chain"],
                    "via": [
                        h.edge.kind.value for h in path.hops if h.edge is not None
                    ],
                    "at": f"{tail.node.file_path}:{tail.node.span.start_line}",
                })

        self._note_dispatch_boundaries(node, envelope)
        return flows

    def _note_dataflow_state(self, node: Node, envelope: Envelope) -> None:
        """Record whether this file's data flow was computed or deferred (§9.2)."""
        row = self.store.conn.execute(
            "SELECT c.dataflow_status FROM coverage c JOIN files f ON f.id = c.file_id "
            "WHERE f.path = ?",
            (node.file_path,),
        ).fetchone()
        status = row["dataflow_status"] if row else "unknown"
        envelope.dataflow = {"status": status, "file": node.file_path}
        if status == "lazy":
            envelope.add_boundary(
                "dataflow_deferred",
                node_id=node.id,
                detail=(
                    f"{node.file_path} indexed with dataflow=lazy; "
                    f"reindex with --dataflow=eager for complete flow"
                ),
            )
        elif status == "unsupported":
            envelope.add_boundary(
                "dataflow_unsupported",
                node_id=node.id,
                detail=f"no data-flow spec for {node.lang}",
            )

    def _note_dispatch_boundaries(self, node: Node, envelope: Envelope) -> None:
        """Surface recorded boundaries touching this node (§9.1)."""
        for boundary in self.store.boundaries_for_nodes([node.id]):
            envelope.add_boundary(
                boundary.kind.value,
                node_id=boundary.node_id,
                detail=boundary.detail or "",
                candidates=boundary.candidates,
            )

    # ---- §8 contract map --------------------------------------------------

    def contract_map(
        self, *, service: str | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        """Who exposes and consumes what, across services.

        A contract with consumers but no exposer is reported as such rather
        than omitted: it means an external dependency, or a bug.
        """
        clauses = ["n.kind = 'contract'"]
        params: list[Any] = []
        if kind:
            clauses.append("e.metadata LIKE ?")
            params.append(f'%"contract_kind":"{kind}"%')

        rows = self.store.conn.execute(
            f"""
            SELECT n.id, n.name, n.signature FROM nodes n
            LEFT JOIN edges e ON e.dst = n.id
            WHERE {' AND '.join(clauses)}
            GROUP BY n.id ORDER BY n.signature
            """,  # noqa: S608 - clauses are literals; values are bound
            params,
        ).fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            exposers: list[dict[str, str]] = []
            consumers: list[dict[str, str]] = []
            contract_kind = ""
            for edge in self.store.edges_to(row["id"]):
                peer = self.store.get_node(edge.src)
                if peer is None:
                    continue
                contract_kind = edge.metadata.get("contract_kind", contract_kind)
                entry = {
                    "node": peer.qualified_name,
                    "service": peer.service_id or "",
                    "lang": peer.lang,
                    "at": f"{peer.file_path}:{peer.span.start_line}",
                }
                if edge.kind is EdgeKind.EXPOSES:
                    exposers.append(entry)
                elif edge.kind is EdgeKind.CONSUMES:
                    consumers.append(entry)

            if service and not any(
                e["service"] == service for e in (*exposers, *consumers)
            ):
                continue

            services = {e["service"] for e in (*exposers, *consumers) if e["service"]}
            out.append({
                "contract": row["signature"] or row["name"],
                "kind": contract_kind,
                "exposed_by": exposers,
                "consumed_by": consumers,
                "cross_service": len(services) > 1,
                "dangling": bool(consumers) and not exposers,
                "unconsumed": bool(exposers) and not consumers,
            })
        return out

    # ---- §10.3 motifs -----------------------------------------------------

    def similar_flows(
        self, exemplar: Node, *, budget: Budget, envelope: Envelope
    ) -> dict[str, Any]:
        """The canonical wiring shape of an exemplar's peers.

        The feature-planning query. Given one existing handler, return the
        template its siblings follow and the extension points a new one would
        need to touch -- not a symbol dump.

        ``intent=plan`` by definition: no data flow, no line-level detail. It is
        the cheapest primitive, and the one to reach for first on "add X".
        """
        kinds = [str(k) for k in (EdgeKind.CALLS, EdgeKind.EXPOSES, EdgeKind.CONSUMES)]
        template = self.traverser.walk(
            exemplar.id, forward=True, kinds=kinds, budget=budget, envelope=envelope
        )
        motif = _dominant_motif(template)

        peers: list[dict[str, Any]] = []
        for exposes in self.store.edges_from(exemplar.id, [EdgeKind.EXPOSES]):
            contract_kind = exposes.metadata.get("contract_kind", "")
            for row in self.store.conn.execute(
                "SELECT src FROM edges WHERE kind = 'EXPOSES' AND metadata LIKE ? "
                "AND src != ? LIMIT ?",
                (f'%"contract_kind":"{contract_kind}"%', exemplar.id, budget.limit),
            ):
                peer = self.store.get_node(row["src"])
                if peer is not None:
                    peers.append({
                        "node": peer.qualified_name,
                        "at": f"{peer.file_path}:{peer.span.start_line}",
                    })

        return {
            "exemplar": exemplar.qualified_name,
            "canonical_shape": motif,
            "peers": peers,
            "extension_points": self._extension_points(exemplar),
        }

    def _extension_points(self, exemplar: Node) -> list[dict[str, Any]]:
        """Places a new instance of this flow would have to be registered.

        Interfaces with several implementations, and contract registries. These
        are the edits a feature plan has to include and which a call graph
        alone does not reveal.
        """
        points: list[dict[str, Any]] = []
        for edge in self.store.edges_from(exemplar.id):
            target = self.store.get_node(edge.dst)
            if target is None:
                continue
            if edge.kind is EdgeKind.IMPLEMENTS:
                implementors = self.store.edges_to(target.id, [EdgeKind.IMPLEMENTS])
                points.append({
                    "kind": "interface",
                    "node": target.qualified_name,
                    "implementations": len(implementors),
                })
            elif edge.kind in (EdgeKind.EXPOSES, EdgeKind.CONSUMES):
                points.append({
                    "kind": "contract_registry",
                    "node": target.signature or target.name,
                    "contract_kind": edge.metadata.get("contract_kind", ""),
                })
        return points

    # ---- §10.2 stack traces ----------------------------------------------

    def map_stacktrace(
        self, text: str, *, budget: Budget, envelope: Envelope
    ) -> list[dict[str, Any]]:
        """Map stack frames onto nodes, innermost first.

        The highest-value entry point for debugging, and cheap once spans
        exist: a frame is a ``file:line``, which is exactly what
        ``nodes_containing_line`` takes.
        """
        frames: list[dict[str, Any]] = []
        for path, line in _parse_frames(text):
            candidates = self._match_file(path)
            matched = False
            for candidate in candidates:
                nodes = self.store.nodes_containing_line(candidate, line)
                if not nodes:
                    continue
                node = nodes[-1]
                frame: dict[str, Any] = {
                    "at": f"{candidate}:{line}",
                    "node": node.qualified_name,
                    "kind": node.kind.value,
                    "operations": [
                        {
                            "kind": e.kind.value,
                            "target": (
                                t.qualified_name
                                if (t := self.store.get_node(e.dst)) else e.dst
                            ),
                        }
                        for e in self.store.edges_on_line(candidate, line)
                    ],
                }
                if budget.wants_source(Intent.PRECISE):
                    from .traverse import source_for

                    frame["source"] = source_for(
                        self.store, [node], self.project_root
                    ).get(node.id, "")
                frames.append(frame)
                matched = True
                break
            if not matched:
                # A frame in a third-party package is genuinely outside the
                # index; saying so is more useful than omitting the frame.
                frames.append({"at": f"{path}:{line}", "node": None,
                               "reason": "not in the index"})
                envelope.add_boundary(
                    "frame_outside_index", detail=f"{path}:{line}"
                )
        return frames

    def _match_file(self, path: str) -> list[str]:
        """Indexed paths a stack-trace path could refer to.

        Frames carry absolute or interpreter-relative paths, so a suffix match
        against indexed paths is the only reliable join.
        """
        row = self.store.conn.execute(
            "SELECT path FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row:
            return [row["path"]]
        tail = path.replace("\\", "/").split("/")[-1]
        return [
            r["path"]
            for r in self.store.conn.execute(
                "SELECT path FROM files WHERE path LIKE ? ORDER BY length(path) LIMIT 5",
                (f"%/{tail}",),
            )
        ]

    # ---- §10.2 explain ----------------------------------------------------

    def explain(self, src: str, dst: str) -> list[dict[str, Any]]:
        """Evidence and provenance for the edges between two nodes."""
        out: list[dict[str, Any]] = []
        for edge in self.store.edges_from(src):
            if edge.dst != dst:
                continue
            out.append({
                "kind": edge.kind.value,
                "evidence_tier": edge.evidence.tier.value,
                "evidence": edge.evidence.label,
                "confidence": edge.evidence.confidence,
                "resolution": edge.resolution.value,
                "provenance": edge.provenance,
                "written_at": (
                    f"{edge.file_path}:{edge.span.start_line}"
                    if edge.span else edge.file_path
                ),
                "metadata": edge.metadata or None,
            })
        return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: Stack-frame shapes across the six languages. Python's `File "x", line N`,
#: and the `at pkg.Cls.m(File.java:42)` / `x.ts:42:9` family.
_FRAME_PATTERNS = (
    re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+)'),
    re.compile(r"\((?P<path>[^():]+):(?P<line>\d+)\)"),
    re.compile(r"at (?P<path>[\w./\\-]+\.\w+):(?P<line>\d+)"),
    re.compile(r"(?P<path>[\w./\\-]+\.(?:py|java|ts|tsx|js|jsx|go|rs|cs)):(?P<line>\d+)"),
)


def _parse_frames(text: str) -> list[tuple[str, int]]:
    """Extract ``(path, line)`` pairs from a stack trace, innermost first.

    Python prints outermost first, so the list is reversed for Python-shaped
    traces; JVM and V8 already print innermost first.
    """
    frames: list[tuple[str, int]] = []
    python_shaped = 'File "' in text
    for line in text.splitlines():
        for pattern in _FRAME_PATTERNS:
            match = pattern.search(line)
            if match:
                frames.append((match.group("path"), int(match.group("line"))))
                break
    if python_shaped:
        frames.reverse()
    # Preserve order while dropping repeats (recursive frames).
    seen: set[tuple[str, int]] = set()
    return [f for f in frames if not (f in seen or seen.add(f))]


def _dominant_motif(paths: Sequence) -> list[str]:
    """The commonest edge-kind sequence among paths, as the canonical shape."""
    counts: dict[tuple[str, ...], int] = {}
    for path in paths:
        signature = path.signature()
        if signature:
            counts[signature] = counts.get(signature, 0) + 1
    if not counts:
        return []
    best = max(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))
    return list(best[0])
