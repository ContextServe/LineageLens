"""The reachability model and its verdict vocabulary.

The question "is this dead code?" used to be answered by "does any relation
target it?", which treats a graph of call edges as a model of Python
reachability. It is not: a request model referenced only from a route signature,
a dependency handed to Depends(), a base class and a name in __all__ are all live
and none are called.

These tests pin the rules that make the answer trustworthy, and in particular the
two rules that must NOT hold -- a reachable class does not resurrect its ordinary
methods, and a dead function's outgoing references rescue nothing.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig
from lineagelens.reachability import IMPLICIT_DUNDERS, compute_reachability


def project(root: Path, files: dict[str, str]) -> Path:
    for relpath, text in files.items():
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def verdicts(root: Path, config: ProjectConfig | None = None):
    config = config or ProjectConfig.load(root)
    graph, _ = analyze(root, config)
    return compute_reachability(graph, config)


# ------------------------------------------------------- the two negative rules
def test_a_reachable_class_does_not_resurrect_its_ordinary_methods(tmp_path):
    """The rule that must not creep in.

    If reaching a class marked all its methods reachable, dead-method detection
    would be gone entirely and the feature would be decorative.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Service:\n"
                "    def __init__(self):\n"
                "        self.x = 1\n"
                "\n"
                "    def used(self):\n"
                "        return 1\n"
                "\n"
                "    def never_used(self):\n"
                "        return 2\n"
                "\n"
                "\n"
                "def main():\n"
                "    return Service().used()\n"
            ),
            "pyproject.toml": (
                '[project]\nname = "p"\nversion = "0"\n\n'
                '[project.scripts]\np = "pkg.mod:main"\n'
            ),
        },
    )
    result = verdicts(root)
    assert result.explain("pkg.mod.Service").verdict == "alive"
    assert result.explain("pkg.mod.Service.used").verdict == "alive"
    assert result.explain("pkg.mod.Service.never_used").verdict == "dead", (
        "an unused method of a reachable class must still be reported"
    )
    # ...but its dunders are kept, because the language calls them.
    assert result.explain("pkg.mod.Service.__init__").verdict == "alive"
    assert result.explain("pkg.mod.Service.__init__").rescue.name == "implicit_dunder"


def test_a_dead_functions_references_rescue_nothing(tmp_path):
    """Reachability is transitive from roots, not "has any incoming edge"."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def target():\n"
                "    return 1\n"
                "\n"
                "\n"
                "class Payload:\n"
                "    pass\n"
                "\n"
                "\n"
                "def orphan(item: Payload):\n"
                "    return target()\n"
            ),
        },
    )
    result = verdicts(root)
    assert result.explain("pkg.mod.orphan").verdict == "dead"
    assert result.explain("pkg.mod.target").verdict == "dead", (
        "target is only called by a dead function, so it is dead too"
    )
    assert result.explain("pkg.mod.Payload").verdict == "dead", (
        "annotated only by a dead function"
    )


# ------------------------------------------------------------ per-mechanism
def test_dunder_methods_are_kept_when_their_class_is_reachable(tmp_path):
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Ctx:\n"
                "    def __enter__(self):\n"
                "        return self\n"
                "\n"
                "    def __exit__(self, *e):\n"
                "        return False\n"
                "\n"
                "\n"
                "def main():\n"
                "    with Ctx():\n"
                "        return 1\n"
            ),
            "pyproject.toml": (
                '[project]\nname = "p"\nversion = "0"\n\n'
                '[project.scripts]\np = "pkg.mod:main"\n'
            ),
        },
    )
    result = verdicts(root)
    for name in ("__enter__", "__exit__"):
        candidate = result.explain(f"pkg.mod.Ctx.{name}")
        assert candidate.verdict == "alive"
        assert candidate.rescue.name == "implicit_dunder"


def test_overrides_of_a_reachable_base_method_are_dynamic_not_dead(tmp_path):
    """Polymorphic dispatch: no call site names the override.

    Reported as dynamic_only rather than alive because it rests on a name match
    up an inheritance chain, and Python has no override keyword.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Base:\n"
                "    def op(self):\n"
                "        return 0\n"
                "\n"
                "\n"
                "class Child(Base):\n"
                "    def op(self):\n"
                "        return 1\n"
                "\n"
                "\n"
                "def consume(thing: Base):\n"
                "    return thing.op()\n"
                "\n"
                "\n"
                "def main():\n"
                "    return consume(Child())\n"
            ),
            "pyproject.toml": (
                '[project]\nname = "p"\nversion = "0"\n\n'
                '[project.scripts]\np = "pkg.mod:main"\n'
            ),
        },
    )
    result = verdicts(root)
    candidate = result.explain("pkg.mod.Child.op")
    assert candidate.verdict == "dynamic_only"
    assert candidate.rescue.name == "polymorphic_override"
    assert candidate.rescue.evidence.tier == "deterministic_heuristic"
    assert candidate.rescue.via_symbol == "pkg.mod.Base.op"


def test_source_reachable_only_from_tests_is_test_only(tmp_path):
    """Materially different from both alive and dead.

    Deleting it breaks the suite, but nothing that ships uses it -- so it is a
    finding, and a different triage decision from either.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "def shipped():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def only_tested():\n"
                "    return 2\n"
                "\n"
                "\n"
                "def main():\n"
                "    return shipped()\n"
            ),
            "tests/test_mod.py": (
                "from pkg.mod import only_tested\n"
                "\n"
                "\n"
                "def test_it():\n"
                "    assert only_tested() == 2\n"
            ),
            "pyproject.toml": (
                '[project]\nname = "p"\nversion = "0"\n\n'
                '[project.scripts]\np = "pkg.mod:main"\n'
            ),
        },
    )
    result = verdicts(root)
    assert result.explain("pkg.mod.shipped").verdict == "alive"
    assert result.explain("pkg.mod.only_tested").verdict == "test_only"


def test_probably_dead_when_an_unresolved_call_shares_the_name(tmp_path):
    """Deadness cannot be asserted when a dynamic call site might be the caller."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": (
                "class Handler:\n"
                "    def process(self):\n"
                "        return 1\n"
                "\n"
                "\n"
                "def dispatch(unknown):\n"
                "    return unknown.process()\n"
            ),
        },
    )
    result = verdicts(root)
    candidate = result.explain("pkg.mod.Handler.process")
    assert candidate.verdict == "probably_dead"
    assert candidate.rescue.name == "name_collision_unresolved_call"
    assert candidate.rescue.evidence.tier == "deterministic_heuristic"


def test_verdict_and_scope_are_independent(tmp_path):
    """Dead code in a test file is a different triage decision from dead code that ships."""
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/mod.py": "def unused_source():\n    return 1\n",
            "tests/helpers.py": "def unused_helper():\n    return 2\n",
        },
    )
    result = verdicts(root)
    source = result.explain("pkg.mod.unused_source")
    test = result.explain("tests.helpers.unused_helper")
    assert (source.verdict, source.scope) == ("dead", "source")
    assert (test.verdict, test.scope) == ("dead", "test")


# --------------------------------------------------------------- audit trail
def test_every_reachable_symbol_names_a_mechanism_and_a_tier(tmp_path):
    """A verdict an agent cannot audit is one it should not act on."""
    corpus = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"
    result = verdicts(corpus)
    for candidate in result.all_symbols():
        if candidate.verdict in ("dead",):
            assert candidate.rescue is None
            continue
        assert candidate.rescue is not None, f"{candidate.symbol.id} has no mechanism"
        assert candidate.rescue.name
        assert candidate.rescue.detail
        assert candidate.rescue.evidence.tier in (
            "deterministic_fact",
            "deterministic_heuristic",
        )


def test_alive_requires_a_fact_tier_mechanism(tmp_path):
    """`alive` and `dynamic_only` differ only in the tier of the reason."""
    corpus = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"
    result = verdicts(corpus)
    for candidate in result.all_symbols():
        if candidate.verdict == "alive":
            assert candidate.rescue.evidence.tier == "deterministic_fact", (
                f"{candidate.symbol.id} is alive on a heuristic mechanism "
                f"({candidate.rescue.name}); it should be dynamic_only"
            )
        if candidate.verdict == "dynamic_only":
            assert candidate.rescue.evidence.tier == "deterministic_heuristic"


def test_candidates_are_ordered_most_severe_first(tmp_path):
    corpus = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"
    order = {"dead": 0, "probably_dead": 1, "test_only": 2}
    seen = [order[c.verdict] for c in verdicts(corpus).candidates()]
    assert seen == sorted(seen)


def test_alive_symbols_are_not_candidates(tmp_path):
    corpus = Path(__file__).resolve().parent / "fixtures" / "reachability_corpus"
    result = verdicts(corpus)
    for candidate in result.candidates():
        assert candidate.verdict not in ("alive", "dynamic_only", "public_api")


# ------------------------------------------------------------------- modes
def test_imports_only_mode_is_stricter_than_the_default(tmp_path):
    """The default over-approximates on purpose; the strict mode is opt-in.

    Treating every module body as executing costs recall -- some real dead code
    stays hidden -- but never fabricates a "this is dead, delete it", which is
    the failure mode that destroys trust.
    """
    root = project(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/orphan_module.py": (
                "def helper():\n    return 1\n\n\nvalue = helper()\n"
            ),
        },
    )
    base = ProjectConfig.load(root)
    default = verdicts(root, base)
    assert default.explain("pkg.orphan_module.helper").verdict == "alive"

    strict = replace(base, analysis=replace(base.analysis, module_scope_roots="imports_only"))
    graph, _ = analyze(root, strict)
    strict_result = compute_reachability(graph, strict)
    assert strict_result.explain("pkg.orphan_module.helper").verdict == "dead", (
        "imports_only must not seed a module body nothing imports"
    )


def test_implicit_dunder_list_covers_the_common_protocols():
    for name in ("__init__", "__enter__", "__exit__", "__getattr__", "__iter__", "__eq__"):
        assert name in IMPLICIT_DUNDERS
    assert "process" not in IMPLICIT_DUNDERS
    assert "run" not in IMPLICIT_DUNDERS
