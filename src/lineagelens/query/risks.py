"""Resiliency risk signals on schema 4 (#60).

Rebuilds what `list_resiliency_risks` provided before `59eb115` deleted
`queries.py` with the rest of the schema-3 core. The product surfaces this most
prominently as:

    [RESILIENCY RISK DETECTED]
    Thread.sleep(1000) in async
    blocking_in_async (High)

Schema 4 can do it better than schema 3 could, because the substrate is already
present rather than inferred: ``NodeFlags.ASYNC`` is populated, ``CALLS`` edges
carry an evidence tier so a risk found through an inferred edge can be reported
with lower confidence than one found through a verified edge, and
``Traverser.walk`` already does budgeted traversal with an envelope.

**The single most important implementation note.** A blocking call is almost
always an *unresolved* reference. ``java.lang.Thread`` and Python's ``time`` are
external and outside the index, so they are never nodes and never the
destination of an edge. A detector that only looks at resolved edges misses the
canonical example in this rule's own name. So ``unresolved_refs`` is searched
alongside the graph, and on this repository that is where every ``time.sleep``
lives.

**Severity is derived, not declared.** A rule's ``severity`` is a base, adjusted
by distance from the async frame, whether that frame is an entry point, and the
weakest evidence tier on the chain. A risk found only through a heuristic edge
is reported at lower confidence -- never suppressed, because suppressing it
would make the absence of a finding mean two different things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core import EdgeKind, NodeFlags
from .budget import Budget, Envelope, QueryResult

#: Severity ladder, weakest first. Derivation moves a finding along it rather
#: than inventing values, so a rule author's `severity` stays meaningful.
SEVERITIES = ("low", "medium", "high", "critical")

#: Edge kinds a blocking call can be reached through. `CALLS` only: a risk
#: reached via `IMPORTS` or `CONTAINS` is not a call path, and including them
#: would turn "reachable from an async frame" into "in the same module as".
RISK_EDGES = (EdgeKind.CALLS.value,)

#: How far to walk from an async frame. Beyond this a blocking call is real but
#: the chain is too long to be actionable, and reporting it crowds out the ones
#: that are.
DEFAULT_RISK_DEPTH = 5


@dataclass(slots=True)
class Finding:
    """One risk, with the evidence for it."""

    rule: str
    severity: str
    summary: str
    #: The blocking call, as written.
    call: str
    #: Where the blocking call is, `file:line`.
    at: str
    #: The async frame it sits inside.
    frame: str
    frame_at: str
    #: Hops from the frame to the call. 0 means the frame calls it directly.
    distance: int
    #: Readable chain from frame to call.
    chain: str
    #: Weakest evidence tier on that chain -- the confidence ceiling.
    evidence: str
    #: Why the severity differs from the rule's base, when it does.
    severity_reasons: list[str] = field(default_factory=list)
    #: True when the call was matched as an unresolved external reference
    #: rather than a graph edge. Not a caveat: it is the normal case.
    external: bool = False

    def as_dict(self) -> dict[str, Any]:
        out = {
            "rule": self.rule,
            "severity": self.severity,
            "summary": self.summary,
            "call": self.call,
            "at": self.at,
            "frame": self.frame,
            "frame_at": self.frame_at,
            "distance": self.distance,
            "chain": self.chain,
            "evidence": self.evidence,
            "external": self.external,
        }
        if self.severity_reasons:
            out["severity_reasons"] = self.severity_reasons
        return out


def _bump(severity: str, steps: int) -> str:
    """Move along :data:`SEVERITIES`, clamped."""
    try:
        index = SEVERITIES.index(severity)
    except ValueError:
        index = SEVERITIES.index("high")
    return SEVERITIES[max(0, min(len(SEVERITIES) - 1, index + steps))]


def _normalise(target: str) -> str:
    """A comparable form for a call target.

    Qualified names use ``/`` and ``#``; rules are written in the language's
    own dotted notation. Both collapse to dot-separated lowercase, and matching
    is on the *suffix*, so ``Thread.sleep`` matches
    ``java.lang.Thread.sleep`` without a rule author having to write every
    import spelling.
    """
    return target.replace("#", ".").replace("/", ".").replace("::", ".").lower()


def _matches(candidate: str, patterns: list[str] | tuple[str, ...]) -> str | None:
    """The pattern ``candidate`` matches on its trailing segments, if any."""
    normalised = _normalise(candidate)
    for pattern in patterns:
        wanted = _normalise(pattern)
        if normalised == wanted or normalised.endswith("." + wanted):
            return pattern
    return None


class RiskDetector:
    """Finds rule violations on one indexed project."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.store = engine.store

    # ---- rule access ------------------------------------------------------

    def _packs(self) -> dict[str, Any]:
        from ..extract.spec import SpecRegistry

        registry = SpecRegistry()
        return {
            lang: registry.spec_for(lang)
            for lang in registry.languages_with_risks()
        }

    def _languages_present(self) -> set[str]:
        return {
            row["lang"]
            for row in self.store.conn.execute(
                "SELECT DISTINCT lang FROM files WHERE lang IS NOT NULL"
            )
        }

    # ---- async frames -----------------------------------------------------

    def _async_frames(self, lang: str, spec: Any) -> list[Any]:
        """Nodes that are async frames in ``lang``.

        Two mechanisms, because `async` is not one concept. A flag where the
        language has a keyword; a marker match where it does not. Java is the
        latter and is the case the product advertises, so it is handled here
        rather than deferred.
        """
        flagged = [
            node for node in self.store.nodes_with_flag(NodeFlags.ASYNC)
            if node.lang == lang
        ]
        if not spec.async_markers:
            return flagged

        by_id = {node.id: node for node in flagged}
        for node in self._nodes_of_lang(lang):
            if node.id in by_id:
                continue
            if any(
                _matches(d, list(spec.async_markers))
                for d in self._annotations_of(node)
            ):
                by_id[node.id] = node
                continue
            # A frame can also be async because of what it calls: a body
            # wrapped in CompletableFuture.supplyAsync has no annotation.
            if self._calls_a_marker(node, list(spec.async_markers)):
                by_id[node.id] = node
        return list(by_id.values())

    def _nodes_of_lang(self, lang: str) -> list[Any]:
        from ..store.db import _node_from_row

        return [
            _node_from_row(row)
            for row in self.store.conn.execute(
                "SELECT * FROM nodes WHERE lang = ? AND kind IN "
                "('function', 'method', 'constructor') ORDER BY id",
                (lang,),
            )
        ]

    def _annotations_of(self, node: Any) -> list[str]:
        """Annotations on a node, from the graph *and* from unresolved refs.

        `@Async` is a Spring annotation, so it is external and outside the
        index -- it arrives as an unresolved ref with `ref_kind='decorate'`
        and `node.decorators` is empty. Reading only the node field found
        nothing, which is the same "resolved edges only" mistake that would
        have missed `Thread.sleep` itself.
        """
        found = list(node.decorators or ())
        found.extend(
            row["ref_text"]
            for row in self.store.conn.execute(
                "SELECT ref_text FROM unresolved_refs "
                "WHERE from_node = ? AND ref_kind = 'decorate'",
                (node.id,),
            )
        )
        return found

    def _calls_a_marker(self, node: Any, markers: list[str]) -> bool:
        for edge in self.store.edges_from(node.id, kinds=RISK_EDGES):
            target = self.store.get_node(edge.dst)
            if target and _matches(target.qualified_name, markers):
                return True
        for ref in self._unresolved_from(node.id):
            if _matches(self._ref_target(ref), markers):
                return True
        return False

    # ---- unresolved references -------------------------------------------

    def _unresolved_from(self, node_id: str) -> list[Any]:
        return self.store.conn.execute(
            "SELECT ref_text, receiver_hint, file_path, line FROM unresolved_refs "
            "WHERE from_node = ? AND ref_kind = 'call' ORDER BY line",
            (node_id,),
        ).fetchall()

    @staticmethod
    def _ref_target(row: Any) -> str:
        """``receiver.name`` when a receiver was inferred, else the bare name.

        `time.sleep` arrives as ref_text='sleep' with receiver_hint='time', so
        rebuilding the dotted form is what lets a rule be written the way a
        programmer would write it.
        """
        hint = row["receiver_hint"]
        return f"{hint}.{row['ref_text']}" if hint else row["ref_text"]

    # ---- detection --------------------------------------------------------

    def find(
        self,
        *,
        min_severity: str | None = None,
        budget: Budget,
        envelope: Envelope,
        max_depth: int = DEFAULT_RISK_DEPTH,
    ) -> tuple[list[Finding], dict[str, str]]:
        """All findings, plus per-language analysis status."""
        packs = self._packs()
        present = self._languages_present()
        status = {
            lang: ("analysed" if lang in packs else "unsupported")
            for lang in sorted(present)
        }

        findings: list[Finding] = []
        for lang in sorted(present & packs.keys()):
            spec = packs[lang]
            for rule in spec.risks or ():
                findings.extend(
                    self._apply(rule, lang, spec, envelope, max_depth)
                )

        floor = SEVERITIES.index(min_severity) if min_severity in SEVERITIES else 0
        findings = [
            f for f in findings if SEVERITIES.index(f.severity) >= floor
        ]
        findings.sort(
            key=lambda f: (-SEVERITIES.index(f.severity), f.distance, f.at)
        )
        return findings[: budget.limit * 2], status

    def _apply(
        self, rule: dict, lang: str, spec: Any, envelope: Envelope, max_depth: int
    ) -> list[Finding]:
        targets = list(rule.get("targets", ()))
        allowed = list(rule.get("allowed", ()))
        if not targets:
            return []

        out: list[Finding] = []
        for frame in self._async_frames(lang, spec):
            out.extend(
                self._walk_frame(frame, rule, targets, allowed, envelope, max_depth)
            )
        return out

    def _walk_frame(
        self,
        frame: Any,
        rule: dict,
        targets: list[str],
        allowed: list[str],
        envelope: Envelope,
        max_depth: int,
    ) -> list[Finding]:
        """Breadth-first from one async frame, collecting blocking calls."""
        base = str(rule.get("severity", "high"))
        rule_id = str(rule.get("id", "unknown"))
        summary = str(rule.get("summary", ""))
        frame_at = f"{frame.file_path}:{frame.span.start_line}"
        is_entry = frame.has(NodeFlags.ENTRY_POINT)

        out: list[Finding] = []
        seen = {frame.id}
        # (node, distance, chain, weakest evidence tier so far)
        frontier: list[tuple[Any, int, list[str], str]] = [
            (frame, 0, [frame.name], "fact")
        ]

        while frontier:
            node, distance, chain, weakest = frontier.pop(0)

            # Unresolved external calls made *by this node*. The canonical
            # case: Thread.sleep and time.sleep are never graph nodes.
            for ref in self._unresolved_from(node.id):
                call = self._ref_target(ref)
                if _matches(call, allowed):
                    continue
                matched = _matches(call, targets)
                if matched is None:
                    continue
                out.append(self._finding(
                    rule_id, base, summary, call,
                    f"{ref['file_path']}:{ref['line']}",
                    frame, frame_at, distance, [*chain, call.split(".")[-1]],
                    weakest, is_entry, external=True,
                ))

            if distance >= max_depth:
                continue

            for edge in self.store.edges_from(node.id, kinds=RISK_EDGES):
                target = self.store.get_node(edge.dst)
                if target is None or target.id in seen:
                    continue
                seen.add(target.id)
                tier = _weaker(weakest, edge.evidence.tier.value)
                next_chain = [*chain, target.name]

                if _matches(target.qualified_name, allowed):
                    continue
                matched = _matches(target.qualified_name, targets)
                if matched is not None:
                    out.append(self._finding(
                        rule_id, base, summary, target.qualified_name,
                        f"{target.file_path}:{target.span.start_line}",
                        frame, frame_at, distance + 1, next_chain, tier,
                        is_entry, external=False,
                    ))
                frontier.append((target, distance + 1, next_chain, tier))
        return out

    @staticmethod
    def _finding(
        rule_id: str, base: str, summary: str, call: str, at: str,
        frame: Any, frame_at: str, distance: int, chain: list[str],
        evidence: str, is_entry: bool, *, external: bool,
    ) -> Finding:
        """Assemble one finding, deriving its severity from the evidence."""
        severity = base
        reasons: list[str] = []

        # A direct call is worse than one five hops down.
        if distance > 2:
            severity = _bump(severity, -1)
            reasons.append(f"{distance} hops from the async frame")

        # A stalled entry point is a user-visible outage.
        if is_entry:
            severity = _bump(severity, 1)
            reasons.append("the async frame is an entry point")

        # Confidence, not truth. Never suppressed: a suppressed finding makes
        # the absence of a finding mean two different things.
        if evidence != "fact":
            severity = _bump(severity, -1)
            reasons.append(f"weakest edge on the chain is {evidence}")

        return Finding(
            rule=rule_id, severity=severity, summary=summary, call=call, at=at,
            frame=frame.qualified_name, frame_at=frame_at, distance=distance,
            chain=" -> ".join(chain), evidence=evidence,
            severity_reasons=reasons, external=external,
        )


#: Tier strength, weakest last. Used to carry a chain's ceiling forward.
_TIER_ORDER = ("fact", "heuristic", "probabilistic")


def _weaker(current: str, candidate: str) -> str:
    def rank(value: str) -> int:
        try:
            return _TIER_ORDER.index(value)
        except ValueError:
            return len(_TIER_ORDER)

    return candidate if rank(candidate) > rank(current) else current


def list_risks(
    engine: Any,
    *,
    min_severity: str | None = None,
    limit: int | None = None,
    max_depth: int | None = None,
) -> QueryResult:
    """``lineagelens query risks`` and the MCP tool of the same name."""
    from ..core import Intent

    _, budget, envelope = engine._prepare(None, Intent.PLAN, limit=limit)
    detector = RiskDetector(engine)
    findings, status = detector.find(
        min_severity=min_severity,
        budget=budget,
        envelope=envelope,
        max_depth=max_depth or DEFAULT_RISK_DEPTH,
    )
    # Named on the envelope so "no risks" and "not analysed" cannot be
    # confused. This is the failure the envelope exists to prevent, and it
    # applies here more than anywhere.
    envelope.dataflow = envelope.dataflow or {}
    return QueryResult.of(
        "resiliency_risks",
        [f.as_dict() for f in findings],
        budget=budget,
        envelope=envelope,
        ranking="severity_then_distance",
        risks={
            "languages": status,
            "unsupported": sorted(
                lang for lang, state in status.items() if state == "unsupported"
            ),
            "max_depth": max_depth or DEFAULT_RISK_DEPTH,
        },
    )


__all__ = [
    "DEFAULT_RISK_DEPTH",
    "RISK_EDGES",
    "SEVERITIES",
    "Finding",
    "RiskDetector",
    "list_risks",
]
