"""Tests that a graph and report survive being written to disk and read back.

Every API surface except the CLI serves from ``.lineagelens/graph.json`` rather
than from an in-memory graph, so anything dropped by ``load_graph`` is invisible
in production while looking perfectly fine in an analyzer-level test.
"""

from __future__ import annotations

import json

from lineagelens.analyzer import analyze
from lineagelens.cli import write_artifacts
from lineagelens.config import ProjectConfig
from lineagelens.queries import find_duplicate_names, load_graph, load_report


def build_project(root):
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "mod.py").write_text(
        "import sqlite3\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    def save(self, conn):\n"
        "        conn.execute('INSERT INTO t VALUES (1)')\n"
        "\n"
        "\n"
        "def helper():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (root / "src" / "pkg" / "other.py").write_text(
        "def helper():\n    return 2\n", encoding="utf-8"
    )
    return root


def test_resiliency_survives_the_round_trip(tmp_path):
    """`/resiliency` and the graph-view risk flag were always empty from disk."""
    root = build_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    in_memory = sum(len(s.resiliency) for s in graph.symbols.values())
    assert in_memory > 0, "fixture produced no resiliency signals to test with"

    write_artifacts(root, config, graph, report, quiet=True)
    reloaded = load_graph(root)

    assert sum(len(s.resiliency) for s in reloaded.symbols.values()) == in_memory
    signal = next(s.resiliency[0] for s in reloaded.symbols.values() if s.resiliency)
    assert signal.evidence.tier in (
        "deterministic_fact",
        "deterministic_heuristic",
        "probabilistic",
    )
    assert signal.category and signal.severity


def test_report_failures_survive_the_round_trip(tmp_path):
    """Without this, a reloaded report is always is_clean() regardless of reality."""
    root = build_project(tmp_path)
    (root / "src" / "pkg" / "broken.py").write_text("def oops(\n", encoding="utf-8")
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    assert report.has_failures()

    write_artifacts(root, config, graph, report, quiet=True)
    reloaded = load_report(root)

    assert reloaded is not None
    assert reloaded.has_failures(), "failures were dropped on load"
    assert not reloaded.is_clean()
    assert reloaded.failures[0].stage == "parse"
    assert reloaded.failures[0].error_type == "SyntaxError"


def test_container_children_are_populated_and_survive(tmp_path):
    """`get_module_overview().submodules` was always empty because nothing wrote children."""
    root = build_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)

    assert graph.containers["pkg.mod"].children, "module container has no child symbols"
    assert "pkg.mod" in graph.containers["pkg"].children, "package does not list its module"

    write_artifacts(root, config, graph, report, quiet=True)
    reloaded = load_graph(root)
    assert reloaded.containers["pkg.mod"].children == graph.containers["pkg.mod"].children


def test_every_symbol_has_a_parent(tmp_path):
    """Top-level classes used to have parent=None and so were dropped by ?module=."""
    root = build_project(tmp_path)
    graph, _ = analyze(root)
    orphans = [s.id for s in graph.symbols.values() if s.parent is None]
    assert orphans == []


def test_duplicate_names_are_keyed_by_kind(tmp_path):
    root = build_project(tmp_path)
    graph, _ = analyze(root)

    duplicates = find_duplicate_names(graph)
    assert ("function", "helper") in duplicates, "two same-kind duplicates not detected"
    assert all(
        isinstance(key, tuple) and len(key) == 2 for key in duplicates
    ), "expected (kind, name) pairs so a method duplicate cannot flag a class"


def test_write_artifacts_emits_both_files(tmp_path):
    root = build_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    graph_file, report_file = write_artifacts(root, config, graph, report, quiet=True)

    assert graph_file.exists() and report_file.exists()
    payload = json.loads(graph_file.read_text())
    assert payload["symbols"] and payload["relations"] and payload["containers"]


def test_stale_schema_is_rejected(tmp_path):
    """A graph.json from a different schema must fail loudly, not silently.

    Phase 2 changed what symbol ids mean, so ids from an older graph are not
    comparable. Serving them would produce confidently wrong answers.
    """
    import json

    import pytest

    from lineagelens.queries import GraphNotFoundError

    root = build_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    graph_file, _ = write_artifacts(root, config, graph, report, quiet=True)

    payload = json.loads(graph_file.read_text())
    payload["schema_version"] = 1
    graph_file.write_text(json.dumps(payload))

    with pytest.raises(GraphNotFoundError, match="schema"):
        load_graph(root)


def test_schema_version_and_project_root_are_persisted(tmp_path):
    import json

    from lineagelens.model import SCHEMA_VERSION

    root = build_project(tmp_path)
    config = ProjectConfig.load(root)
    graph, report = analyze(root, config)
    graph_file, _ = write_artifacts(root, config, graph, report, quiet=True)

    payload = json.loads(graph_file.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["project_root"] == str(root.resolve())
