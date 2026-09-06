"""Integration tests for hybrid engine, config parsing, and CLI dispatcher."""

from pathlib import Path
from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig, AnalysisConfig
from lineagelens.model import CodeGraph, Evidence, Relation, Symbol
from lineagelens.hybrid_merger import HybridGraphMerger


def test_hybrid_graph_merger_evidence_upgrade(tmp_path: Path):
    base = CodeGraph(str(tmp_path))
    base.add_symbol(Symbol("app.py::foo", "function", "foo", "app.py", 1, "app"))
    base.add_symbol(Symbol("app.py::bar", "function", "bar", "app.py", 5, "app"))
    base.add_relation(Relation(
        source="app.py::foo",
        target="app.py::bar",
        kind="CALLS",
        file="app.py",
        line=2,
        evidence=Evidence(tier="deterministic_heuristic", label="tree_sitter_name_match"),
    ))

    scip = CodeGraph(str(tmp_path))
    scip.add_symbol(Symbol("app.py::foo", "function", "foo", "app.py", 1, "app"))
    scip.add_symbol(Symbol("app.py::bar", "function", "bar", "app.py", 5, "app"))
    scip.add_relation(Relation(
        source="app.py::foo",
        target="app.py::bar",
        kind="CALLS",
        file="app.py",
        line=2,
        evidence=Evidence(tier="deterministic_fact", label="scip_compiler"),
    ))

    merged = HybridGraphMerger.merge(base, scip)
    assert len(merged.relations) == 1
    assert merged.relations[0].evidence.tier == "deterministic_fact"
    assert merged.relations[0].evidence.label == "scip_compiler"


def test_analyze_with_engine_flag(tmp_path: Path):
    file1 = tmp_path / "main.py"
    file1.write_text("def run():\n    pass\n", encoding="utf-8")

    config = ProjectConfig(source_roots=(".",), analysis=AnalysisConfig(engine="tree-sitter"))
    graph, report = analyze(tmp_path, config)

    assert report.symbols_found >= 1
    assert "main.py::run" in graph.symbols
