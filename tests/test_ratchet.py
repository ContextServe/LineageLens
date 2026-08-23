"""The dead-code ratchet.

An absolute gate on dead-code count cannot be adopted: every existing codebase
fails it on day one, so it gets switched off and nothing improves. A ratchet is
adoptable immediately because it only fails on candidates that are new relative
to a committed baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig
from lineagelens.ratchet import Baseline, evaluate, severity_at_or_above
from lineagelens.reachability import compute_reachability


def project(root: Path, files: dict[str, str]) -> Path:
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def candidates_for(root: Path):
    config = ProjectConfig.load(root)
    graph, _ = analyze(root, config)
    return compute_reachability(graph, config).candidates()


BASE = {
    "src/pkg/__init__.py": "",
    "src/pkg/mod.py": "def already_dead():\n    return 1\n",
}


def test_a_clean_baseline_passes(tmp_path):
    root = project(tmp_path, BASE)
    candidates = candidates_for(root)
    baseline = Baseline(entries={c.symbol.id: c.verdict for c in candidates})

    result = evaluate(candidates, baseline)
    assert not result.introduced
    assert not result.failed(max_new=0)


def test_newly_dead_code_fails(tmp_path):
    root = project(tmp_path, BASE)
    baseline = Baseline(entries={c.symbol.id: c.verdict for c in candidates_for(root)})

    (root / "src" / "pkg" / "mod.py").write_text(
        "def already_dead():\n    return 1\n\n\ndef newly_dead():\n    return 2\n",
        encoding="utf-8",
    )
    result = evaluate(candidates_for(root), baseline)

    assert set(result.introduced) == {"pkg.mod.newly_dead"}
    assert result.failed(max_new=0)
    assert not result.failed(max_new=1), "--max-new must tolerate the allowance"


def test_pre_existing_dead_code_does_not_fail(tmp_path):
    """The whole point: adoptable without a cleanup first."""
    root = project(tmp_path, BASE)
    candidates = candidates_for(root)
    assert candidates, "fixture has no dead code to accept"
    baseline = Baseline(entries={c.symbol.id: c.verdict for c in candidates})
    assert not evaluate(candidates, baseline).failed(max_new=0)


def test_removing_dead_code_is_reported_as_resolved(tmp_path):
    root = project(tmp_path, BASE)
    baseline = Baseline(entries={c.symbol.id: c.verdict for c in candidates_for(root)})

    (root / "src" / "pkg" / "mod.py").write_text("", encoding="utf-8")
    result = evaluate(candidates_for(root), baseline)

    assert "pkg.mod.already_dead" in result.resolved
    assert not result.failed(max_new=0)


def test_a_deletion_cannot_mask_an_addition(tmp_path):
    """Counting alone would let a new dead function hide behind a removed one."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": "def old_dead():\n    return 1\n",
        },
    )
    baseline = Baseline(entries={c.symbol.id: c.verdict for c in candidates_for(root)})

    (root / "src" / "pkg" / "mod.py").write_text(
        "def new_dead():\n    return 1\n", encoding="utf-8"
    )
    result = evaluate(candidates_for(root), baseline)

    assert len(result.current) == len(baseline.entries), "count is unchanged"
    assert result.failed(max_new=0), "but the ratchet must still fail"
    assert set(result.introduced) == {"pkg.mod.new_dead"}


def test_a_verdict_getting_worse_fails(tmp_path):
    baseline = Baseline(entries={"pkg.mod.thing": "probably_dead"})

    class _Symbol:
        id = "pkg.mod.thing"

    class _Candidate:
        symbol = _Symbol()
        verdict = "dead"

    result = evaluate([_Candidate()], baseline, fail_on="probably_dead")
    assert result.worsened == {"pkg.mod.thing": ("probably_dead", "dead")}
    assert result.failed(max_new=0)


def test_fail_on_threshold_narrows_what_counts(tmp_path):
    assert severity_at_or_above("dead") == {"dead"}
    assert severity_at_or_above("probably_dead") == {"dead", "probably_dead"}
    assert "test_only" in severity_at_or_above("test_only")
    # An unknown threshold must not silently widen the gate.
    assert severity_at_or_above("nonsense") == {"dead"}


def test_baseline_round_trips(tmp_path):
    path = tmp_path / "baseline.json"
    Baseline(entries={"a.b": "dead", "c.d": "probably_dead"}).save(path)

    payload = json.loads(path.read_text())
    assert payload["entries"] == {"a.b": "dead", "c.d": "probably_dead"}
    assert "note" in payload, "the file should explain itself to whoever reviews it"
    assert Baseline.load(path).entries == {"a.b": "dead", "c.d": "probably_dead"}


def test_a_missing_or_corrupt_baseline_is_treated_as_empty(tmp_path):
    assert Baseline.load(tmp_path / "absent.json").entries == {}
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert Baseline.load(corrupt).entries == {}


def test_the_pragma_removes_a_symbol_from_the_ratchet(tmp_path):
    """The documented escape hatch has to actually satisfy the gate."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": "def loaded_by_config():\n    return 1\n",
        },
    )
    before = {c.symbol.id for c in candidates_for(root)}
    assert "pkg.mod.loaded_by_config" in before

    (root / "src" / "pkg" / "mod.py").write_text(
        "def loaded_by_config():  # lineagelens: keep\n    return 1\n", encoding="utf-8"
    )
    after = {c.symbol.id for c in candidates_for(root)}
    assert "pkg.mod.loaded_by_config" not in after
