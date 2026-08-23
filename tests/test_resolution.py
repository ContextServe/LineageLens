"""Regression tests for call-target resolution.

Each test here corresponds to a specific defect found in the resolution pipeline.
The names of the evidence labels matter as much as the targets: a relation that
resolves to the right symbol at the wrong trust tier is still a bug, because the
reachability verdicts downstream key off the tier.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lineagelens.analyzer import Relationships, SymbolIndex, analyze


def write_project(root: Path, files: dict[str, str], config: str | None = None) -> Path:
    """Materialise a throwaway project and return its root."""
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if config is not None:
        (root / "lineagelens.yaml").write_text(config, encoding="utf-8")
    return root


def relations_to(graph, suffix: str) -> list:
    return [r for r in graph.relations if r.target.endswith(suffix)]


# --------------------------------------------------------------------------- 1a
def test_same_named_classes_in_different_modules_do_not_cross_resolve(tmp_path):
    """Two classes sharing a name must not steal each other's incoming edges.

    An unanchored `sym_id.endswith(full_name)` test returned whichever candidate
    came first in dict order, silently attributing the call to the wrong class.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/alpha.py": "class Engine:\n    def run(self):\n        return 1\n",
            "src/pkg/beta.py": "class Engine:\n    def run(self):\n        return 2\n",
            "src/pkg/caller.py": (
                "from pkg.beta import Engine\n"
                "\n"
                "def go():\n"
                "    engine = Engine()\n"
                "    return engine.run()\n"
            ),
        },
    )
    graph, _ = analyze(root)

    run_edges = [r for r in graph.relations if r.target.endswith("Engine.run")]
    assert run_edges, "the method call produced no relation at all"
    assert {r.target for r in run_edges} == {"pkg.beta.Engine.run"}, (
        f"resolved to {sorted({r.target for r in run_edges})}; pkg.alpha.Engine.run is a "
        "different class and must not receive this edge"
    )


def test_symbol_index_suffix_keys_are_dot_anchored(tmp_path):
    """`MyEngine` must never match a lookup for `Engine`."""
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": "class MyEngine:\n    pass\n",
        },
    )
    graph, _ = analyze(root)
    index = SymbolIndex.build(graph)

    assert "MyEngine" in index.by_suffix
    assert "Engine" not in index.by_suffix
    assert index.resolve_target(graph, "src/pkg/mod.py", 999, "Engine") == (None, False)


def test_ambiguous_inference_result_is_declined_not_guessed(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "class Dup:\n    pass\n",
            "src/pkg/b.py": "class Dup:\n    pass\n",
        },
    )
    graph, _ = analyze(root)
    index = SymbolIndex.build(graph)

    symbol_id, ambiguous = index.resolve_target(graph, "src/pkg/nowhere.py", 999, "Dup")
    assert symbol_id is None
    assert ambiguous is True, "two equally-good candidates must report ambiguity"


# --------------------------------------------------------------------------- 1b
def test_jedi_position_for_multiline_attribute_chain_uses_end_line():
    """The trailing `.attr` lives at the end of the expression, so must the line.

    Pairing `lineno` (start) with a column derived from `end_col_offset` (end)
    points at a position on the wrong line for any chain that wraps.
    """
    source = "result = (some_object\n          .apply_migrations())\n"
    call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))

    line, column = Relationships._jedi_position(call.func)

    assert line == 2, f"expected the line holding '.apply_migrations', got {line}"
    assert source.splitlines()[line - 1][column:].startswith("apply_migrations")


def test_jedi_position_handles_column_zero():
    """Column 0 is falsy; a truthiness test on it silently fell back to col_offset."""
    source = "o.m()\n"
    call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))
    line, column = Relationships._jedi_position(call.func)
    assert (line, column) == (1, 2)


# --------------------------------------------------------------------------- 1d
def test_import_table_does_not_shadow_a_local_binding(tmp_path):
    """An import entry must not preclude a richer local binding for the same name.

    `from db import session` used to return external immediately, so the local
    `session = Session()` binding was never consulted.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/db.py": (
                "class Session:\n"
                "    def query(self):\n"
                "        return []\n"
                "\n"
                "session = None\n"
            ),
            "src/pkg/use.py": (
                "from pkg.db import Session, session\n"
                "\n"
                "def run():\n"
                "    session = Session()\n"
                "    return session.query()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert relations_to(graph, "pkg.db.Session.query"), (
        "the local `session = Session()` binding was shadowed by the import table"
    )


# --------------------------------------------------------------------------- 1e
def test_bound_method_arguments_are_not_shifted_by_self(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Repo:\n"
                "    def fetch(self, key, limit):\n"
                "        return (key, limit)\n"
                "\n"
                "def go():\n"
                "    repo = Repo()\n"
                "    return repo.fetch('k', 5)\n"
            ),
        },
    )
    graph, _ = analyze(root)
    edge = relations_to(graph, "pkg.mod.Repo.fetch")
    assert edge, "bound method call produced no relation"
    params = [a["parameter"] for a in edge[0].arguments]
    assert params == ["key", "limit"], f"got {params}; `self` absorbed a real argument"


# --------------------------------------------------------------------------- 1j
def test_annotated_parameter_passthrough_binds_self_attribute(tmp_path):
    """`def __init__(self, p: Base): self.p = p` then `self.p.op()`.

    This is the most common attribute-binding shape in real code and it must
    resolve statically -- tier deterministic_fact, no inference engine involved.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Provider:\n"
                "    def fetch(self):\n"
                "        return 1\n"
                "\n"
                "class Service:\n"
                "    def __init__(self, provider: Provider):\n"
                "        self.provider = provider\n"
                "\n"
                "    def run(self):\n"
                "        return self.provider.fetch()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    edges = relations_to(graph, "pkg.mod.Provider.fetch")
    assert edges, "self.provider.fetch() did not resolve"
    edge = edges[0]
    assert edge.resolution_evidence.label == "annotated_parameter_passthrough"
    assert edge.resolution_evidence.tier == "deterministic_fact", (
        "a binding read off a literal annotation is a fact, not a heuristic"
    )


# ------------------------------------------------------- trust-tier ordering
def test_static_resolution_wins_over_inference(tmp_path):
    """A call resolvable by scope walk must be labelled as a fact, not an inference.

    Consulting the inference engine first demoted ~1100 provable edges from
    deterministic_fact to deterministic_heuristic on a real project.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "def caller():\n"
                "    return helper()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    edge = relations_to(graph, "pkg.mod.helper")
    assert edge
    assert edge[0].resolution_evidence.label == "static_scope_walk"
    assert edge[0].resolution == "resolved"


def test_reexport_through_package_init_resolves_to_the_definition(tmp_path):
    """Importing from a package that re-exports must reach the defining module.

    The import table builds `pkg.sub.name`, which is not a symbol; only following
    the re-export finds `pkg.sub.impl.name`.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/sub/__init__.py": "from .impl import worker\n\n__all__ = ['worker']\n",
            "src/pkg/sub/impl.py": "def worker():\n    return 1\n",
            "src/pkg/caller.py": (
                "from pkg.sub import worker\n"
                "\n"
                "def go():\n"
                "    return worker()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert "pkg.sub.impl.worker" in graph.symbols
    assert relations_to(graph, "pkg.sub.impl.worker"), (
        "the re-exported function shows zero callers; only the package-level "
        "`pkg.sub.worker` name was tried"
    )


# ------------------------------------------------- module-scope source node
def test_module_level_call_produces_a_relation(tmp_path):
    """Statements outside any function used to produce no relation at all.

    Not even an unresolved one -- which hid every bit of wiring: `app = App()`,
    `include_router(...)`, registry population, `if __name__ == "__main__"`.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def build():\n"
                "    return 1\n"
                "\n"
                "app = build()\n"
            ),
        },
    )
    graph, _ = analyze(root)

    edges = relations_to(graph, "pkg.mod.build")
    assert edges, "module-level call produced no relation"
    assert edges[0].source == "pkg.mod.<module>"


def test_main_guard_call_is_attributed_to_module_scope(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def run_it():\n"
                "    return 1\n"
                "\n"
                "\n"
                'if __name__ == "__main__":\n'
                "    run_it()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    edges = relations_to(graph, "pkg.mod.run_it")
    assert edges and edges[0].source == "pkg.mod.<module>"


def test_class_body_call_is_attributed_to_the_class(tmp_path):
    """A statement in a class body belongs to the class, not to the module."""
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def default_factory():\n"
                "    return 1\n"
                "\n"
                "\n"
                "class Config:\n"
                "    value = default_factory()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    edges = relations_to(graph, "pkg.mod.default_factory")
    assert edges and edges[0].source == "pkg.mod.Config"


def test_module_scope_node_does_not_steal_line_one_definitions(tmp_path):
    """The synthetic node sits at line 1, where the first real def also lives.

    Indexing it by location let `<module>` win that (file, line) key, so a call
    to a function defined on line 1 resolved to the module node instead.
    """
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/first.py": "def on_line_one():\n    return 1\n",
            "src/pkg/caller.py": (
                "from pkg.first import on_line_one\n"
                "\n"
                "def go():\n"
                "    return on_line_one()\n"
            ),
        },
    )
    graph, _ = analyze(root)
    targets = {r.target for r in graph.relations if r.source == "pkg.caller.go"}
    assert "pkg.first.on_line_one" in targets
    assert "pkg.first.<module>" not in targets


def test_module_scope_nodes_exist_for_every_module(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "x = 1\n",
            "src/pkg/b.py": "y = 2\n",
        },
    )
    graph, _ = analyze(root)
    scope_nodes = {s.id for s in graph.symbols.values() if s.kind == "module_scope"}
    assert scope_nodes == {"pkg.<module>", "pkg.a.<module>", "pkg.b.<module>"}
    for node_id in scope_nodes:
        assert graph.symbols[node_id].parent == node_id.rsplit(".", 1)[0]
