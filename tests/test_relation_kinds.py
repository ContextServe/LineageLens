"""The non-call relation kinds, and the volume guards on them.

Reachability in Python does not flow through calls alone. A class used only as a
type annotation, a function handed to a dependency injector, a name in __all__,
a base class -- all live code that no call edge describes. These tests assert
each kind fires on its own minimal case, and that adding them did not disturb
call extraction or explode the graph.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from lineagelens.analyzer import analyze, is_name_chain, iter_python
from lineagelens.config import ProjectConfig

CORPUS = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"


def project(root: Path, files: dict[str, str]) -> Path:
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def edges(graph, kind: str | None = None, target: str | None = None):
    return [
        r
        for r in graph.relations
        if (kind is None or r.kind == kind) and (target is None or r.target == target)
    ]


# --------------------------------------------------------------- call parity
def test_every_ast_call_still_produces_exactly_one_relation():
    """Adding reference visitors must not swallow or duplicate a call.

    The first version of the attribute visitor stopped descending whenever the
    immediate `.value` was an Attribute. That is wrong for a chain rooted in a
    call -- `pd.to_datetime(x).dt.date` -- and silently dropped 31 call sites on
    a real project.
    """
    config = ProjectConfig.load(CORPUS)
    census = 0
    for path in iter_python(
        CORPUS, [*config.source_roots, *config.test_roots, *config.script_roots]
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        census += sum(1 for node in ast.walk(tree) if isinstance(node, ast.Call))

    graph, _ = analyze(CORPUS, config)
    produced = len(
        [r for r in graph.relations if r.kind in ("CALLS", "AWAIT_CALLS", "CREATES_TASK")]
    )
    assert produced == census, f"{census} ast.Call nodes but {produced} call relations"


def test_call_nested_in_an_attribute_chain_is_not_lost(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def make():\n"
                "    return None\n"
                "\n"
                "def go():\n"
                "    return make().attr.other\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert edges(graph, "CALLS", "pkg.mod.make"), "call inside an attribute chain was skipped"


def test_reference_extraction_filters_most_candidate_name_loads():
    """Tripwire against a reference-edge explosion.

    An unfiltered version of REFERENCES emits 27,522 edges on a real project --
    more than twice its call count -- which would bloat the graph and make the
    visualisation unusable. The "must resolve to an in-repo Symbol" filter is
    load-bearing, not an optimisation.

    A ratio against the call count is the wrong instrument for this corpus, which
    is deliberately reference-dense; measuring against candidate name loads tests
    the filter directly. The corpus sits near 36% because almost every name in it
    is a fixture that resolves on purpose; a real project is nearer 5%.
    """
    config = ProjectConfig.load(CORPUS)
    candidates = 0
    for path in iter_python(
        CORPUS, [*config.source_roots, *config.test_roots, *config.script_roots]
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Name, ast.Attribute)):
                continue
            if not isinstance(getattr(node, "ctx", None), ast.Load):
                continue
            if id(node) in called:
                continue
            if isinstance(node, ast.Name) or is_name_chain(node):
                candidates += 1

    graph, _ = analyze(CORPUS, config)
    emitted = len([r for r in graph.relations if r.kind == "REFERENCES"])
    assert emitted <= candidates * 0.6, (
        f"{emitted} reference edges from {candidates} candidate name loads: the "
        "in-repo-only filter is not doing its job"
    )


def test_no_duplicate_reference_edges_for_one_syntactic_site():
    """An attribute chain must not also emit an edge for its own prefix.

    Restricted to reference-style kinds: one line can legitimately contain two
    calls to the same target -- `len(a) + len(b)` -- so call edges are expected
    to repeat.
    """
    graph, _ = analyze(CORPUS, ProjectConfig.load(CORPUS))
    reference_kinds = {"REFERENCES", "ANNOTATES", "INHERITS", "DECORATES", "EXPORTS", "IMPORTS"}
    seen = set()
    duplicates = []
    for relation in graph.relations:
        if relation.kind not in reference_kinds:
            continue
        key = (relation.source, relation.target, relation.kind, relation.file, relation.line)
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    assert not duplicates, f"duplicate reference edges: {duplicates[:5]}"


# ------------------------------------------------------------------ per kind
def test_references_link_a_function_passed_as_a_value(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def handler():\n"
                "    return 1\n"
                "\n"
                "def register(fn):\n"
                "    return fn\n"
                "\n"
                "HANDLERS = {'a': handler}\n"
                "registered = register(handler)\n"
            ),
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "REFERENCES", "pkg.mod.handler")
    assert found, "a function used as a dict value and as an argument has no reference edge"
    assert {r.source for r in found} == {"pkg.mod.<module>"}


def test_references_are_not_emitted_for_unresolvable_names(tmp_path):
    """No `external:` targets for references -- an unresolvable name is not evidence."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "import os\n"
                "\n"
                "def go(thing):\n"
                "    return os.sep + thing.whatever\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert not [r for r in graph.relations if r.kind == "REFERENCES"]


def test_annotates_reaches_a_class_used_only_in_a_signature(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Payload:\n"
                "    pass\n"
                "\n"
                "class Result:\n"
                "    pass\n"
                "\n"
                "def handle(item: Payload) -> Result:\n"
                "    raise NotImplementedError\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert edges(graph, "ANNOTATES", "pkg.mod.Payload")
    assert edges(graph, "ANNOTATES", "pkg.mod.Result")


def test_annotates_descends_into_subscripts_and_forward_refs(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "from typing import Optional\n"
                "\n"
                "class Nested:\n"
                "    pass\n"
                "\n"
                "class Later:\n"
                "    pass\n"
                "\n"
                "def a(items: Optional[list[Nested]]) -> None:\n"
                "    return None\n"
                "\n"
                "def b() -> 'Later':\n"
                "    raise NotImplementedError\n"
            ),
        },
    )
    graph, _ = analyze(root)
    assert edges(graph, "ANNOTATES", "pkg.mod.Nested"), "Optional[list[X]] did not reach X"
    assert edges(graph, "ANNOTATES", "pkg.mod.Later"), "string forward reference did not resolve"


def test_inherits_links_subclass_to_base(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/base.py": "class Base:\n    pass\n",
            "src/pkg/child.py": "from pkg.base import Base\n\n\nclass Child(Base):\n    pass\n",
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "INHERITS", "pkg.base.Base")
    assert found and found[0].source == "pkg.child.Child"


def test_decorates_links_decorated_symbol_to_a_local_decorator(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def memoize(fn):\n"
                "    return fn\n"
                "\n"
                "@memoize\n"
                "def cached():\n"
                "    return 1\n"
                "\n"
                "@memoize\n"
                "class Thing:\n"
                "    pass\n"
            ),
        },
    )
    graph, _ = analyze(root)
    sources = {r.source for r in edges(graph, "DECORATES", "pkg.mod.memoize")}
    assert sources == {"pkg.mod.cached", "pkg.mod.Thing"}


def test_exports_links_module_scope_to_dunder_all_entries(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "from .impl import worker\n\n__all__ = ['worker']\n",
            "src/pkg/impl.py": "def worker():\n    return 1\n",
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "EXPORTS", "pkg.impl.worker")
    assert found, "a re-exported symbol has no export edge"
    assert found[0].source == "pkg.<module>"


def test_relative_import_inside_a_package_init_resolves(tmp_path):
    """In pkg/__init__.py the module name is already `pkg`, so `.x` means `pkg.x`.

    Treating it like a regular module stripped a level and produced `x.y`, which
    resolves to nothing -- and re-exports through __init__.py are exactly where
    that matters.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "from .impl import worker\n",
            "src/pkg/impl.py": "def worker():\n    return 1\n",
        },
    )
    graph, _ = analyze(root)
    assert edges(graph, "REFERENCES", "pkg.impl.worker")


def test_imports_link_module_scopes(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "x = 1\n",
            "src/pkg/b.py": "import pkg.a\n",
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "IMPORTS", "pkg.a.<module>")
    assert found and found[0].source == "pkg.b.<module>"


# ------------------------------------------------------------------- toggles
def test_each_kind_can_be_disabled_individually():
    config = ProjectConfig.load(CORPUS)
    full, _ = analyze(CORPUS, config)
    present = {r.kind for r in full.relations} - {"CALLS", "AWAIT_CALLS", "CREATES_TASK"}
    assert present, "corpus produced no non-call relations to toggle"

    for kind in sorted(present):
        remaining = tuple(k for k in config.analysis.relation_kinds if k != kind)
        narrowed = replace(config, analysis=replace(config.analysis, relation_kinds=remaining))
        graph, _ = analyze(CORPUS, narrowed)
        assert not [r for r in graph.relations if r.kind == kind], f"{kind} still emitted when disabled"


def test_disabling_all_new_kinds_leaves_calls_untouched():
    config = ProjectConfig.load(CORPUS)
    full, _ = analyze(CORPUS, config)
    off = replace(config, analysis=replace(config.analysis, relation_kinds=()))
    bare, _ = analyze(CORPUS, off)

    call_kinds = ("CALLS", "AWAIT_CALLS", "CREATES_TASK")
    assert len([r for r in bare.relations if r.kind in call_kinds]) == len(
        [r for r in full.relations if r.kind in call_kinds]
    )
    assert {r.kind for r in bare.relations} <= set(call_kinds)


def test_overrides_links_a_subclass_method_to_the_base_declaration(tmp_path):
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
                "\n"
                "class Child(Base):\n"
                "    def op(self):\n"
                "        return 1\n"
            ),
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "OVERRIDES", "pkg.mod.Base.op")
    assert found and found[0].source == "pkg.mod.Child.op"
    assert found[0].resolution_evidence.tier == "deterministic_heuristic", (
        "Python has no override keyword; this is a name match and must say so"
    )


def test_overrides_follows_a_transitive_base_chain(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class A:\n"
                "    def op(self):\n"
                "        return 1\n"
                "\n"
                "\n"
                "class B(A):\n"
                "    pass\n"
                "\n"
                "\n"
                "class C(B):\n"
                "    def op(self):\n"
                "        return 2\n"
            ),
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "OVERRIDES", "pkg.mod.A.op")
    assert found and found[0].source == "pkg.mod.C.op", "grandparent declaration not reached"


def test_overrides_terminates_on_an_inheritance_cycle(tmp_path):
    """Not legal Python, but a graph can contain one via a bad resolution."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class A:\n"
                "    def op(self):\n"
                "        return 1\n"
                "\n"
                "\n"
                "class B(A):\n"
                "    def op(self):\n"
                "        return 2\n"
            ),
        },
    )
    graph, _ = analyze(root)
    graph.add_relation(
        type(graph.relations[0])(
            source="pkg.mod.A",
            target="pkg.mod.B",
            kind="INHERITS",
            file="src/pkg/mod.py",
            line=1,
        )
    )
    from lineagelens.analyzer import emit_overrides

    emit_overrides(graph, ProjectConfig())  # must return, not spin
    assert edges(graph, "OVERRIDES")


def test_uses_fixture_links_a_test_to_a_fixture_by_parameter_name(tmp_path):
    """pytest injects by name; nothing in the test's source references the fixture."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "tests/conftest.py": (
                "import pytest\n"
                "\n"
                "\n"
                "@pytest.fixture\n"
                "def db():\n"
                "    return 'db'\n"
            ),
            "tests/test_thing.py": "def test_it(db):\n    assert db == 'db'\n",
        },
    )
    graph, _ = analyze(root)
    found = edges(graph, "USES_FIXTURE", "tests.conftest.db")
    assert found and found[0].source == "tests.test_thing.test_it"
    assert found[0].resolution_evidence.label == "pytest_fixture_name"


def test_string_references_are_off_by_default():
    """A false rescue hides dead code with no trail, so this must be opt-in."""
    config = ProjectConfig.load(CORPUS)
    assert "REFERENCES_STRING" not in config.analysis.relation_kinds
    graph, _ = analyze(CORPUS, config)
    assert not edges(graph, "REFERENCES_STRING")


def test_string_references_resolve_an_unambiguous_dotted_literal():
    config = ProjectConfig.load(CORPUS)
    enabled = replace(
        config,
        analysis=replace(
            config.analysis,
            relation_kinds=(*config.analysis.relation_kinds, "REFERENCES_STRING"),
        ),
    )
    graph, _ = analyze(CORPUS, enabled)
    found = edges(graph, "REFERENCES_STRING", "corpus.tasks.do_work")
    assert found, "an unambiguous dotted literal naming a symbol was not matched"
    assert found[0].evidence.tier == "deterministic_heuristic"


def test_string_references_decline_an_ambiguous_name(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "def worker():\n    return 1\n",
            "src/pkg/b.py": "def worker():\n    return 2\n",
            "src/pkg/uses.py": "TARGET = 'pkg.a.worker'\nAMBIGUOUS = 'worker'\n",
        },
    )
    base = ProjectConfig.load(root)
    enabled = replace(
        base,
        analysis=replace(base.analysis, relation_kinds=(*base.analysis.relation_kinds, "REFERENCES_STRING")),
    )
    graph, _ = analyze(root, enabled)
    # The fully-qualified literal is unambiguous and resolves.
    assert edges(graph, "REFERENCES_STRING", "pkg.a.worker")
    # A bare name matching two symbols must resolve to neither.
    assert not edges(graph, "REFERENCES_STRING", "pkg.b.worker")
