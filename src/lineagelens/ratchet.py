"""Dead-code baseline and ratchet.

A hard gate on absolute dead-code count cannot be adopted: any existing codebase
fails it on day one, so the gate gets disabled and nothing improves. A ratchet
can be adopted the day it is installed -- it only fails on candidates that are
*new* relative to a committed baseline, so a team ships the feature immediately
and the number only moves one way.

The baseline stores symbol ids and verdicts, not counts. Counts alone would let a
newly-dead function hide behind a deleted one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import SCHEMA_VERSION

BASELINE_NAME = "dead-code-baseline.json"

#: Verdicts ordered by severity. `--fail-on` names the least severe verdict that
#: should fail, so `--fail-on probably_dead` also covers `dead`.
SEVERITY = ("dead", "probably_dead", "test_only", "dynamic_only")


@dataclass
class Baseline:
    """Accepted dead-code candidates for a project."""

    entries: dict[str, str] = field(default_factory=dict)  # symbol id -> verdict
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> Baseline:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            entries=dict(raw.get("entries") or {}),
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.schema_version,
            "note": (
                "Accepted dead-code candidates. `lineagelens check` fails only on "
                "entries not listed here, so this number can go down but not up. "
                "Regenerate with `lineagelens check --update-baseline`."
            ),
            "entries": dict(sorted(self.entries.items())),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def severity_at_or_above(threshold: str) -> set[str]:
    """Verdicts at least as severe as ``threshold``."""
    if threshold not in SEVERITY:
        return {"dead"}
    return set(SEVERITY[: SEVERITY.index(threshold) + 1])


@dataclass
class RatchetResult:
    """What changed relative to the baseline."""

    introduced: dict[str, str] = field(default_factory=dict)
    resolved: dict[str, str] = field(default_factory=dict)
    worsened: dict[str, tuple[str, str]] = field(default_factory=dict)
    current: dict[str, str] = field(default_factory=dict)

    def failed(self, max_new: int) -> bool:
        return len(self.introduced) + len(self.worsened) > max_new

    def summary_lines(self) -> list[str]:
        lines = [
            f"Dead-code ratchet: {len(self.current)} candidate(s) at or above the threshold."
        ]
        if self.introduced:
            lines.append(f"\n  New ({len(self.introduced)}):")
            for symbol_id, verdict in sorted(self.introduced.items()):
                lines.append(f"    {verdict:14s} {symbol_id}")
        if self.worsened:
            lines.append(f"\n  Worsened ({len(self.worsened)}):")
            for symbol_id, (was, now) in sorted(self.worsened.items()):
                lines.append(f"    {was} -> {now}  {symbol_id}")
        if self.resolved:
            lines.append(
                f"\n  Resolved ({len(self.resolved)}) -- run with --update-baseline to bank it."
            )
        if not (self.introduced or self.worsened):
            lines.append("\n  Nothing new. ✅")
        return lines


def evaluate(
    candidates: list[Any], baseline: Baseline, fail_on: str = "dead"
) -> RatchetResult:
    """Compare current candidates against the baseline.

    Only verdicts at or above ``fail_on`` are considered, so a project can adopt
    the ratchet on `dead` alone and tighten to `probably_dead` later.
    """
    considered = severity_at_or_above(fail_on)
    current = {
        candidate.symbol.id: candidate.verdict
        for candidate in candidates
        if candidate.verdict in considered
    }

    result = RatchetResult(current=current)
    for symbol_id, verdict in current.items():
        previous = baseline.entries.get(symbol_id)
        if previous is None:
            result.introduced[symbol_id] = verdict
        elif previous != verdict and SEVERITY.index(verdict) < SEVERITY.index(previous):
            # e.g. was probably_dead, now provably dead
            result.worsened[symbol_id] = (previous, verdict)

    for symbol_id, verdict in baseline.entries.items():
        if symbol_id not in current:
            result.resolved[symbol_id] = verdict

    return result


__all__ = [
    "BASELINE_NAME",
    "SEVERITY",
    "Baseline",
    "RatchetResult",
    "evaluate",
    "severity_at_or_above",
]
