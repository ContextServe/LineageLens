"""Entry-point detection rules, one test per rule.

Entry points are the roots of the reachability walk, so a missing root does not
degrade gracefully -- everything downstream of it looks dead. Conversely, every
broadening trades dead-code recall for precision: six new rule families could
take the candidate list to near zero, which looks like success and is not. So
each rule is asserted to fire on its own fixture *and* to be switchable off.
"""

from __future__ import annotations

import collections
from dataclasses import replace
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig

CORPUS = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"


def project(root: Path, files: dict[str, str]) -> Path:
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def kinds_of(graph, symbol_id: str) -> list[str]:
    return graph.symbols[symbol_id].entry_point_kinds


# ------------------------------------------------------------ multiple kinds
def test_a_symbol_can_carry_several_entry_point_kinds(tmp_path):
    """A single slot lost information: sequential ifs overwrote each other.

    A test_ function that also carries a route decorator is both, and which one
    you saw depended on which rule happened to run last.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "tests/test_api.py": (
                "class _R:\n"
                "    def get(self, path):\n"
                "        def d(fn):\n"
                "            return fn\n"
                "        return d\n"
                "\n"
                "router = _R()\n"
                "\n"
                "@router.get('/x')\n"
                "def test_both():\n"
                "    return 1\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert set(kinds_of(graph, "tests.test_api.test_both")) == {"api_route", "test"}


# --------------------------------------------------------------- per-rule
def test_route_decorators_are_not_gated_on_framework_detection(tmp_path):
    """The decorator suffix is the signal; gating it on config caused silent misses.

    A project whose lineagelens.yaml did not happen to list `fastapi` reported
    every one of its routes as dead code.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/api.py": (
                "class _R:\n"
                "    def post(self, path):\n"
                "        def d(fn):\n"
                "            return fn\n"
                "        return d\n"
                "\n"
                "router = _R()\n"
                "\n"
                "@router.post('/items')\n"
                "def create_item():\n"
                "    return 1\n"
            ),
        },
    )
    base = ProjectConfig.load(root)
    no_frameworks = replace(base, frameworks=())
    graph, _ = analyze(root, no_frameworks)
    assert kinds_of(graph, "pkg.api.create_item") == ["api_route"]


def test_pytest_rules_are_name_based_not_directory_based(tmp_path):
    """pytest collects test_*.py wherever it lives, so detection must too."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/test_inline.py": "def test_here():\n    assert True\n",
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.test_inline.test_here") == ["test"]


def test_pytest_fixtures_are_entry_points(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef db():\n    return 1\n",
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "tests.conftest.db") == ["test_fixture"]


def test_unittest_hooks_are_entry_points(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "tests/test_case.py": (
                "import unittest\n"
                "\n"
                "\n"
                "class TestThing(unittest.TestCase):\n"
                "    def setUp(self):\n"
                "        self.x = 1\n"
                "\n"
                "    def tearDown(self):\n"
                "        self.x = None\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "tests.test_case.TestThing.setUp") == ["test_hook"]
    assert kinds_of(graph, "tests.test_case.TestThing.tearDown") == ["test_hook"]


def test_celery_tasks_are_entry_points(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/tasks.py": (
                "class _App:\n"
                "    def task(self, fn):\n"
                "        return fn\n"
                "\n"
                "app = _App()\n"
                "\n"
                "@app.task\n"
                "def do_work():\n"
                "    return 1\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.tasks.do_work") == ["task"]


def test_abstract_methods_are_flagged(tmp_path):
    """An abstract declaration has no callers by design."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "from abc import ABC, abstractmethod\n"
                "\n"
                "\n"
                "class Base(ABC):\n"
                "    @abstractmethod\n"
                "    def op(self):\n"
                "        ...\n"
                "\n"
                "    def concrete(self):\n"
                "        return 1\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert graph.symbols["pkg.mod.Base.op"].is_abstract
    assert not graph.symbols["pkg.mod.Base.concrete"].is_abstract


def test_django_management_command_handle_is_an_entry_point(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/management/__init__.py": "",
            "src/pkg/management/commands/__init__.py": "",
            "src/pkg/management/commands/sync.py": (
                "class Command:\n"
                "    def handle(self, *args, **options):\n"
                "        return 1\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.management.commands.sync.Command.handle") == ["django_command"]


def test_main_guard_marks_the_module_scope_node(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/entry.py": (
                "def run():\n"
                "    return 1\n"
                "\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    run()\n"
            ),
            "src/pkg/plain.py": "def other():\n    return 1\n",
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.entry.<module>") == ["main_module"]
    assert kinds_of(graph, "pkg.plain.<module>") == []


def test_console_script_target_is_an_entry_point(tmp_path):
    root = project(
        tmp_path,
        {
            "pyproject.toml": (
                "[project]\n"
                'name = "thing"\n'
                'version = "0.1.0"\n'
                "\n"
                "[project.scripts]\n"
                'thing = "pkg.cli:main"\n'
            ),
            "src/pkg/__init__.py": "",
            "src/pkg/cli.py": "def main():\n    return 0\n\n\ndef other():\n    return 1\n",
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.cli.main") == ["console_script"]
    assert kinds_of(graph, "pkg.cli.other") == []


def test_pragma_keeps_a_symbol_with_no_other_reference():
    graph, _ = analyze(CORPUS, ProjectConfig.load(CORPUS))
    assert kinds_of(graph, "corpus.pragma.kept_by_pragma") == ["pragma_keep"]
    assert kinds_of(graph, "corpus.pragma.kept_by_pragma_on_decorator") == [], (
        "the control must stay unmarked, proving the pragma is what does the work"
    )


# ---------------------------------------------------------------- toggles
def test_every_rule_can_be_switched_off():
    config = ProjectConfig.load(CORPUS)
    full, _ = analyze(CORPUS, config)
    baseline = collections.Counter(
        k for s in full.symbols.values() for k in s.entry_point_kinds
    )
    assert baseline, "corpus detected no entry points"

    for rule in sorted(config.analysis.entry_point_rules):
        narrowed = replace(
            config,
            analysis=replace(
                config.analysis,
                entry_point_rules={**config.analysis.entry_point_rules, rule: False},
            ),
        )
        graph, _ = analyze(CORPUS, narrowed)
        after = collections.Counter(
            k for s in graph.symbols.values() for k in s.entry_point_kinds
        )
        assert sum(after.values()) <= sum(baseline.values()), (
            f"disabling {rule} somehow produced more entry points"
        )


def test_disabling_all_rules_yields_no_entry_points():
    config = ProjectConfig.load(CORPUS)
    off = replace(
        config,
        analysis=replace(
            config.analysis,
            entry_point_rules={k: False for k in config.analysis.entry_point_rules},
        ),
    )
    graph, _ = analyze(CORPUS, off)
    assert not [s for s in graph.symbols.values() if s.entry_point_kinds]


def test_visitor_methods_are_dispatch_entry_points(tmp_path):
    """ast.NodeVisitor.visit does getattr(self, "visit_" + type name).

    No call site ever names visit_Call, and because the visitor methods are the
    entry to the traversal, treating them as dead cascades through everything
    they call. Found by running LineageLens on itself.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/walker.py": (
                "import ast\n"
                "\n"
                "\n"
                "class Walker(ast.NodeVisitor):\n"
                "    def visit_Call(self, node):\n"
                "        return self.helper()\n"
                "\n"
                "    def helper(self):\n"
                "        return 1\n"
                "\n"
                "    def not_a_visitor(self):\n"
                "        return 2\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.walker.Walker.visit_Call") == ["visitor_dispatch"]
    assert kinds_of(graph, "pkg.walker.Walker.not_a_visitor") == [], (
        "only visit_* methods are dispatch targets"
    )


def test_visitor_detection_follows_an_intermediate_base(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/base.py": "import ast\n\n\nclass Mid(ast.NodeVisitor):\n    pass\n",
            "src/pkg/leaf.py": (
                "from pkg.base import Mid\n"
                "\n"
                "\n"
                "class Leaf(Mid):\n"
                "    def visit_Name(self, node):\n"
                "        return 1\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert kinds_of(graph, "pkg.leaf.Leaf.visit_Name") == ["visitor_dispatch"]


def test_visitor_detection_terminates_on_a_cyclic_base_chain(tmp_path):
    """Not legal Python, but a graph can hold one via a bad resolution."""
    from lineagelens.entrypoints import _mark_visitor_methods

    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class A(B):\n"
                "    def visit_X(self, node):\n"
                "        return 1\n"
                "\n"
                "\n"
                "class B(A):\n"
                "    pass\n"
            ),
        },
    )
    graph, _ = analyze(root)
    _mark_visitor_methods(graph)  # must return rather than recurse forever
    assert "pkg.mod.A.visit_X" in graph.symbols


def test_visitor_rule_is_switchable(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/walker.py": (
                "import ast\n"
                "\n"
                "\n"
                "class Walker(ast.NodeVisitor):\n"
                "    def visit_Call(self, node):\n"
                "        return 1\n"
            ),
        },
    )
    base = ProjectConfig.load(root)
    off = replace(
        base,
        analysis=replace(
            base.analysis,
            entry_point_rules={**base.analysis.entry_point_rules, "visitor_dispatch": False},
        ),
    )
    graph, _ = analyze(root, off)
    assert kinds_of(graph, "pkg.walker.Walker.visit_Call") == []


def test_a_custom_entry_point_family_does_not_disable_the_others(tmp_path):
    """Listing one family in the config used to replace the whole mapping.

    A project that customised api_route silently stopped detecting tests, tasks
    and framework callbacks, and then reported all of them as dead code.
    """
    root = project(
        tmp_path,
        {
            "lineagelens.yaml": (
                "analysis:\n"
                "  entry_points:\n"
                "    api_route:\n"
                "    - .handle\n"
            ),
            "src/pkg/__init__.py": "",
            "tests/test_thing.py": "def test_it():\n    assert True\n",
        },
    )
    config = ProjectConfig.load(root)
    assert config.analysis.entry_points["api_route"] == (".handle",), "custom family applied"
    assert config.analysis.entry_points["cli_command"], "other families keep their defaults"
    assert "task" in config.analysis.entry_points

    graph, _ = analyze(root, config)
    assert kinds_of(graph, "tests.test_thing.test_it") == ["test"]
