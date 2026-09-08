"""Intent, budgets, and the completeness envelope (issue #51 §10.1, §10.5, §10.6).

Three separate controls that are easy to conflate:

* **Intent** selects *what kind* of detail is computed. A bug fix needs
  line-exact spans and full data flow; a feature plan needs the shape of the
  existing wiring and nothing else. This is the primary cost control, and the
  saving comes from not computing detail the question does not need -- not from
  truncating detail it does.
* **Budget** caps *how much* is returned, and records when it truncated.
  Schema 3's ``get_impact`` returned 328 of 759 symbols as an unbounded flat
  list with no indication it had stopped short of nothing.
* **Envelope** states what the answer does *not* cover. An agent that knows a
  trace crossed two dynamic-dispatch boundaries and one unparsed file knows when
  to stop trusting the graph and read the file instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core import Intent

#: Default ceiling on returned items. Chosen to fit comfortably in a response
#: without truncating the common case; a hub symbol with 300 callers truncates
#: and says so, which is the honest outcome.
DEFAULT_LIMIT = 40

#: Default traversal depth. Deep enough for a realistic call chain, shallow
#: enough that a cycle-rich graph cannot produce a runaway walk.
DEFAULT_DEPTH = 8

#: Ceiling on distinct paths returned by ``find_paths``. Several routes between
#: two symbols is informative; a hundred is a different question.
DEFAULT_MAX_PATHS = 12


@dataclass(frozen=True, slots=True)
class Budget:
    """How much a query may return."""

    limit: int = DEFAULT_LIMIT
    max_depth: int = DEFAULT_DEPTH
    max_paths: int = DEFAULT_MAX_PATHS
    #: Include verbatim source for each returned node. Defaults follow intent:
    #: on for ``precise``, off for ``plan``.
    include_source: bool | None = None

    @classmethod
    def for_intent(cls, intent: Intent, **overrides: Any) -> Budget:
        """Budget defaults appropriate to an intent.

        ``plan`` gets a wider item limit but no source and a shallower walk: a
        survey wants breadth cheaply. ``precise`` gets fewer items with full
        detail on each, because a debugging answer is only useful if it is
        exact.
        """
        if intent is Intent.PLAN:
            base = {"limit": 60, "max_depth": 6, "max_paths": 6,
                    "include_source": False}
        else:
            base = {"limit": 25, "max_depth": DEFAULT_DEPTH,
                    "max_paths": DEFAULT_MAX_PATHS, "include_source": True}
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)

    def wants_source(self, intent: Intent) -> bool:
        if self.include_source is not None:
            return self.include_source
        return intent is Intent.PRECISE


@dataclass(slots=True)
class Envelope:
    """What an answer does not cover (§10.6).

    Accumulated during a query rather than computed after it, so it reflects
    what the traversal actually touched instead of repository-wide totals. A
    repository-wide figure would be technically true and useless: 8% of all
    references unresolved says nothing about whether *this* trace is complete.
    """

    intent: Intent = Intent.PLAN
    #: Boundary counts by kind, for boundaries the walk actually crossed.
    boundaries: dict[str, int] = field(default_factory=dict)
    #: Candidate sets at dispatch points, so a reader can see the ambiguity.
    boundary_detail: list[dict[str, Any]] = field(default_factory=list)
    #: Languages present in the touched slice and the tier that resolved them.
    tier_used: dict[str, str] = field(default_factory=dict)
    #: Languages indexed at Tier A only via an explicit override (§7.2).
    degraded: list[str] = field(default_factory=list)
    #: Files in the touched slice that were skipped or partially parsed.
    files: dict[str, int] = field(default_factory=dict)
    #: Reference resolution over the touched files.
    refs: dict[str, Any] = field(default_factory=dict)
    #: Data-flow state for the touched slice (§9.2).
    dataflow: dict[str, Any] = field(default_factory=dict)
    #: Frameworks present but unmodelled (§8.2), so an empty contract answer is
    #: not mistaken for "these services are not connected".
    unclaimed_frameworks: list[dict[str, Any]] = field(default_factory=list)

    def add_boundary(
        self, kind: str, node_id: str = "", detail: str = "",
        candidates: tuple[str, ...] = (),
    ) -> None:
        self.boundaries[kind] = self.boundaries.get(kind, 0) + 1
        if detail and len(self.boundary_detail) < 20:
            entry: dict[str, Any] = {"kind": kind, "detail": detail}
            if node_id:
                entry["node"] = node_id
            if candidates:
                entry["candidates"] = list(candidates[:8])
                entry["candidate_count"] = len(candidates)
            self.boundary_detail.append(entry)

    @property
    def complete(self) -> bool:
        """True when nothing limited this answer.

        Deliberately strict: any boundary, any unparsed file, any deferred data
        flow makes it False. A reader should have to opt into treating a
        qualified answer as complete.
        """
        return (
            not self.boundaries
            and not self.degraded
            and self.files.get("partial", 0) == 0
            and self.files.get("skipped", 0) == 0
        )

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "intent": self.intent.value,
            "complete": self.complete,
        }
        if self.files:
            out["files"] = dict(sorted(self.files.items()))
        if self.refs:
            out["refs"] = self.refs
        if self.boundaries:
            out["boundaries"] = dict(sorted(self.boundaries.items()))
            out["boundary_detail"] = self.boundary_detail
        if self.tier_used:
            out["tier_used"] = dict(sorted(self.tier_used.items()))
        if self.degraded:
            out["degraded"] = sorted(self.degraded)
        if self.dataflow:
            out["dataflow"] = self.dataflow
        if self.unclaimed_frameworks:
            out["unclaimed_frameworks"] = self.unclaimed_frameworks
        return out


@dataclass(slots=True)
class QueryResult:
    """A budgeted answer plus its envelope.

    Every primitive returns this shape, so a caller never has to guess whether
    a short list means "that is all there is" or "we stopped".
    """

    kind: str
    results: list[Any] = field(default_factory=list)
    truncated: bool = False
    total_available: int = 0
    returned: int = 0
    ranking: str = ""
    envelope: Envelope = field(default_factory=Envelope)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        kind: str,
        items: list[Any],
        *,
        budget: Budget,
        envelope: Envelope,
        ranking: str = "",
        **extra: Any,
    ) -> QueryResult:
        """Apply the budget and record whether it bit."""
        total = len(items)
        kept = items[: budget.limit]
        return cls(
            kind=kind,
            results=kept,
            truncated=total > len(kept),
            total_available=total,
            returned=len(kept),
            ranking=ranking,
            envelope=envelope,
            extra={k: v for k, v in extra.items() if v is not None},
        )

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "returned": self.returned,
            "total_available": self.total_available,
            "truncated": self.truncated,
            "results": [
                r.as_dict() if hasattr(r, "as_dict") else r for r in self.results
            ],
            "coverage": self.envelope.as_dict(),
        }
        if self.ranking:
            out["ranking"] = self.ranking
        out.update(self.extra)
        return out
