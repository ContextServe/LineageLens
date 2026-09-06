"""Unit tests for TreeSitterAnalyzer engine."""

from pathlib import Path

from lineagelens.config import AnalysisConfig, ProjectConfig
from lineagelens.treesitter_analyzer import TreeSitterAnalyzer


def test_treesitter_analyzer_basic(tmp_path: Path):
    # Create sample Python file
    py_file = tmp_path / "sample.py"
    py_file.write_text("""def greet(name):\n    return f'Hello {name}'\n\nclass User:\n    def get_name(self):\n        return greet('world')\n""", encoding="utf-8")

    config = ProjectConfig(source_roots=(".",), analysis=AnalysisConfig(engine="tree-sitter"))
    analyzer = TreeSitterAnalyzer(config)
    graph = analyzer.analyze_project(tmp_path)

    assert len(graph.symbols) >= 2
    assert "sample.py::greet" in graph.symbols
    assert "sample.py::User" in graph.symbols
    assert "sample.py::User.get_name" in graph.symbols


def test_treesitter_analyzer_fallback(tmp_path: Path):
    java_file = tmp_path / "App.java"
    java_file.write_text("""public class App {\n    public static void main(String[] args) {\n        System.out.println("Hello");\n    }\n}\n""", encoding="utf-8")

    config = ProjectConfig(source_roots=(".",), analysis=AnalysisConfig(engine="tree-sitter"))
    analyzer = TreeSitterAnalyzer(config)
    graph = analyzer.analyze_project(tmp_path)

    assert "App.java::App" in graph.symbols
