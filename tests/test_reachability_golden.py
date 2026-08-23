"""Golden reachability table for the fixture corpus.

``tests/fixtures/reachability_corpus`` contains one fixture per mechanism by
which reachability flows through Python, and ``EXPECTED.yaml`` records the
verdict, rescue mechanism and scope each symbol should end up with.

Two assertions run at every stage of the work:

* the analyzed symbol set matches the table exactly, in both directions, so a
  new symbol cannot slip in unclassified;
* the false-positive count against the table only ever goes down.

The per-symbol verdict assertions activate once ``lineagelens.reachability``
exists; until then they skip with a reason rather than silently passing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig
from lineagelens.queries import list_unreferenced_symbols

CORPUS = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"

# Symbol ids that are known to be wrong today, mapped to what they should be.
# Each entry is a defect with a scheduled fix; the tests below fail if an entry
# becomes unnecessary, so this map cannot rot into a permanent allowlist.
PENDING_ID_FIXES: dict[str, str] = {}

# The number of symbols the *current* dead-code logic gets wrong relative to
# EXPECTED.yaml. This is a ratchet: lower it as phases land, never raise it.
#
# 29 false positives, 0 false negatives at the point the corpus landed. The gap
# decomposes cleanly into the remaining work, by the mechanism that should have
# rescued each symbol:
#
#   static_call                 6   module-scope call sites produce no relation
#   passed_as_value             6   no REFERENCES edge for a name used as a value
#   implicit_dunder             6   no rule that a reachable class keeps its dunders
#   type_annotation             3   no ANNOTATES edge
#   decorator                   2   no DECORATES edge
#   polymorphic_override        2   no OVERRIDES edge
#   entry_point:*               3   console_script / celery task / pytest fixture
#   dunder_all_export           1   no EXPORTS edge
#
# Progress so far, all measured against the same table:
#   29  corpus landed
#   23  module-scope source node closed the 6 static_call cases
#   10  the non-call relation kinds closed passed_as_value, type_annotation,
#       decorator and dunder_all_export
#
# The remaining 10 are 6 implicit dunders and 2 polymorphic overrides (both need
# the reachability walk, which applies them at dequeue time rather than as plain
# edges) plus 2 entry-point rules (console_script and celery task).
#
# Synthetic module-scope nodes are excluded from the count -- see below.
MAX_FALSE_VERDICTS = 10


@pytest.fixture(scope="module")
def corpus_graph():
    return analyze(CORPUS, ProjectConfig.load(CORPUS))[0]


@pytest.fixture(scope="module")
def expected() -> dict[str, dict]:
    return yaml.safe_load((CORPUS / "EXPECTED.yaml").read_text(encoding="utf-8"))


def canonical(symbol_id: str) -> str:
    """Apply the pending-fix map so the table can describe the intended ids."""
    return PENDING_ID_FIXES.get(symbol_id, symbol_id)


def test_corpus_analyzes_without_failures(corpus_graph):
    graph = corpus_graph
    assert graph.symbols, "corpus produced no symbols"


def test_pending_id_fixes_are_still_needed(corpus_graph):
    """Delete entries from PENDING_ID_FIXES once the underlying defect is fixed."""
    actual = set(corpus_graph.symbols)
    stale = [wrong for wrong in PENDING_ID_FIXES if wrong not in actual]
    assert not stale, (
        f"PENDING_ID_FIXES entries no longer occur and must be removed: {stale}"
    )


def test_every_symbol_is_classified(corpus_graph, expected):
    """Two-way set equality: no unclassified symbols, no stale table entries."""
    actual = {canonical(sid) for sid in corpus_graph.symbols}
    declared = set(expected)

    unclassified = sorted(actual - declared)
    stale = sorted(declared - actual)
    assert not unclassified and not stale, (
        "corpus and EXPECTED.yaml disagree.\n"
        f"  in the graph but not in the table ({len(unclassified)}): {unclassified}\n"
        f"  in the table but not in the graph ({len(stale)}): {stale}"
    )


def test_expected_scopes_match_is_test_path(corpus_graph, expected):
    """The declared scope must agree with how the analyzer classifies the file."""
    from lineagelens.queries import is_test_path

    config = ProjectConfig.load(CORPUS)
    mismatched = []
    for sid, symbol in corpus_graph.symbols.items():
        entry = expected.get(canonical(sid))
        if entry is None:
            continue
        actual = "test" if is_test_path(symbol.file, test_roots=config.test_roots) else "source"
        if actual != entry["scope"]:
            mismatched.append(f"{canonical(sid)}: table says {entry['scope']}, analyzer says {actual}")
    assert not mismatched, "scope disagreements:\n  " + "\n  ".join(mismatched)


def test_dead_code_false_verdicts_are_ratcheting_down(corpus_graph, expected):
    """Measure today's error rate against the table and hold the line.

    The current dead-code query has no notion of reachability, so it flags
    anything with no incoming CALLS edge. This test quantifies that gap and
    prevents it widening.
    """
    flagged = {c.symbol.id for c in list_unreferenced_symbols(corpus_graph)}

    false_positives, false_negatives = [], []
    for sid, symbol in corpus_graph.symbols.items():
        # Synthetic module-scope nodes are reachability *roots* by construction.
        # The pre-reachability query has no concept of a root, so it flags all of
        # them; counting that as error would swamp the signal this ratchet exists
        # to track. They are asserted properly by the verdict test below.
        if symbol.kind == "module_scope":
            continue
        key = canonical(sid)
        entry = expected.get(key)
        if entry is None:
            continue
        should_be_dead = entry["verdict"] == "dead"
        is_flagged = sid in flagged
        if is_flagged and not should_be_dead:
            false_positives.append(key)
        elif should_be_dead and not is_flagged:
            false_negatives.append(key)

    total = len(false_positives) + len(false_negatives)
    assert total <= MAX_FALSE_VERDICTS, (
        f"{total} wrong verdicts, ratchet allows {MAX_FALSE_VERDICTS}.\n"
        f"  false positives ({len(false_positives)}): {sorted(false_positives)}\n"
        f"  false negatives ({len(false_negatives)}): {sorted(false_negatives)}"
    )


def test_control_group_is_never_rescued(corpus_graph, expected):
    """Symbols declared dead must stay dead however permissive the rules get.

    Broadening reachability trades recall for precision. Without this test a
    rule that rescues everything looks like a win.
    """
    flagged = {c.symbol.id for c in list_unreferenced_symbols(corpus_graph)}
    controls = [sid for sid, e in expected.items() if e["verdict"] == "dead"]
    assert controls, "table declares no dead controls"

    reverse = {canonical(sid): sid for sid in corpus_graph.symbols}
    rescued = [c for c in controls if reverse.get(c) and reverse[c] not in flagged]
    assert not rescued, f"control-group symbols were rescued: {sorted(rescued)}"


# --------------------------------------------------------------------------
# Activates in the reachability phase.
# --------------------------------------------------------------------------
def test_verdicts_and_rescue_mechanisms(corpus_graph, expected):
    reachability = pytest.importorskip(
        "lineagelens.reachability",
        reason="reachability model not implemented yet; the table above is its spec",
    )

    result = reachability.compute_reachability(corpus_graph, ProjectConfig.load(CORPUS))
    verdicts = {c.symbol.id: c for c in result.all_symbols()}

    wrong = []
    for sid in corpus_graph.symbols:
        key = canonical(sid)
        entry = expected.get(key)
        if entry is None:
            continue
        got = verdicts.get(sid)
        assert got is not None, f"{key} missing from the reachability result"
        rescue = got.rescue.name if got.rescue else None
        if (got.verdict, rescue) != (entry["verdict"], entry["rescue"]):
            wrong.append(
                f"{key}: expected ({entry['verdict']}, {entry['rescue']}), "
                f"got ({got.verdict}, {rescue})"
            )
    assert not wrong, "verdict mismatches:\n  " + "\n  ".join(wrong)
