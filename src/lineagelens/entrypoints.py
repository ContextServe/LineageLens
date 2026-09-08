"""Entry-point detection: the roots of the reachability walk.

An entry point is code invoked from outside the project -- an HTTP route, a CLI
command, a test, a scheduled task. Reachability is computed *from* these, so a
missing root does not degrade gracefully: everything downstream of it looks dead.

The rules previously lived as fourteen lines inside the analyzer and missed most
real roots. Two of them were also gated on framework auto-detection, so a project
whose ``lineagelens.yaml`` did not list ``fastapi`` silently reported all its
routes as dead code.

Every rule is individually switchable through ``analysis.entry_point_rules``,
because broadening this trades dead-code recall for precision: six new rule
families could take the candidate list to near zero, which looks like success and
is not.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .config import ProjectConfig
from .model import CodeGraph, Symbol

# `# lineagelens: keep` on the definition line or any of its decorators. The
# escape hatch of last resort, for code only reachable in ways static analysis
# cannot see.
PRAGMA = re.compile(r"(#|//|/\*)\s*lineagelens:\s*keep\b")

# pytest's own file-naming rules, which are name-based rather than
# directory-based. Requiring a configured test root missed test files elsewhere.
TEST_FILE = re.compile(r"(^test_.*\.py$)|(.*_test\.py$)|(^conftest\.py$)")

UNITTEST_HOOKS = frozenset(
    {"setUp", "tearDown", "setUpClass", "tearDownClass", "setUpModule", "tearDownModule"}
)

FIXTURE_DECORATORS = frozenset({"fixture", "async_fixture"})


def _decorator_names(node: ast.AST) -> list[str]:
    """Dotted names of a definition's decorators, unwrapping any call."""
    from .analyzer import dotted

    names = []
    for item in getattr(node, "decorator_list", ()):
        expr = item.func if isinstance(item, ast.Call) else item
        name = dotted(expr)
        if name:
            names.append(name)
    return names


def is_test_file(path: Path) -> bool:
    return bool(TEST_FILE.match(path.name))


def _matches(decorators: list[str], suffixes: tuple[str, ...]) -> bool:
    return any(name.endswith(suffix) or name == suffix.lstrip(".") for name in decorators for suffix in suffixes)


def detect(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    module_path: Path,
    class_stack: list[str],
    config: ProjectConfig,
    source_lines: list[str],
) -> list[str]:
    """Every entry-point kind that applies to one function definition."""
    rules = config.analysis.entry_point_rules
    families = config.analysis.entry_points
    decorators = _decorator_names(node)
    kinds: list[str] = []

    # Decorator families. Deliberately not gated on framework auto-detection: the
    # suffix list *is* the signal, and gating it on a value `init` may not have
    # detected caused silent under-detection of every route in the project.
    if rules.get("decorators", True):
        for family in ("api_route", "cli_command", "framework_callback"):
            if _matches(decorators, families.get(family, ())):
                kinds.append(family)

    if rules.get("celery", True) and _matches(decorators, families.get("task", ())):
        kinds.append("task")

    if rules.get("singledispatch", True) and _matches(decorators, families.get("dispatch_registration", ())):
        kinds.append("dispatch_registration")

    if rules.get("pytest", True) and is_test_file(module_path):
        if any(name.split(".")[-1] in FIXTURE_DECORATORS for name in decorators):
            kinds.append("test_fixture")
        elif node.name.startswith("test_") or (class_stack and class_stack[-1].startswith("Test") and node.name.startswith("test_")):
            kinds.append("test")

    if rules.get("unittest", True) and node.name in UNITTEST_HOOKS:
        kinds.append("test_hook")

    if rules.get("django", True):
        parts = module_path.parts
        if node.name == "handle" and "management" in parts and "commands" in parts:
            kinds.append("django_command")
        elif node.name == "ready" and class_stack:
            kinds.append("framework_callback")

    if rules.get("pragma", True) and _has_pragma(node, source_lines):
        kinds.append("pragma_keep")

    return kinds


def _has_pragma(node: ast.AST, source_lines: list[str]) -> bool:
    """Whether `# lineagelens: keep` sits on the definition or a decorator line."""
    lines = [getattr(node, "lineno", 0)]
    lines += [getattr(item, "lineno", 0) for item in getattr(node, "decorator_list", ())]
    for number in lines:
        if 1 <= number <= len(source_lines) and PRAGMA.search(source_lines[number - 1]):
            return True
    return False


def is_abstract(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether a method is declared abstract.

    An abstract declaration has no callers by design -- subclasses implement it
    and callers go through the base type -- so it is never a dead-code candidate.
    """
    return any(
        name.split(".")[-1] in ("abstractmethod", "abstractproperty")
        for name in _decorator_names(node)
    )


#: Base classes that dispatch to methods by computed name. ``ast.NodeVisitor.visit``
#: does ``getattr(self, "visit_" + node.__class__.__name__)``, so no call site ever
#: names ``visit_Call`` -- and because the visitor methods are the entry to the
#: whole traversal, treating them as dead cascades through everything they call.
VISITOR_BASES = ("NodeVisitor", "NodeTransformer")
VISITOR_PREFIX = "visit_"


def mark_project_roots(graph: CodeGraph, root: Path, config: ProjectConfig) -> None:
    """Roots that need project-level context rather than a single definition."""
    rules = config.analysis.entry_point_rules

    if rules.get("main_module", True):
        _mark_main_modules(graph, root)
    if rules.get("console_scripts", True):
        _mark_console_scripts(graph, root)
    if rules.get("visitor_dispatch", True):
        _mark_visitor_methods(graph)


def _mark_visitor_methods(graph: CodeGraph) -> None:
    """Mark ``visit_*`` methods of visitor subclasses as dispatch targets.

    Needs the whole graph rather than a single definition, because the decision
    depends on the class's base list and on transitively inheriting from a
    visitor base within the project.
    """
    members: dict[str, list[Symbol]] = {}
    for symbol in graph.symbols.values():
        if symbol.kind in ("method", "function") and symbol.parent:
            members.setdefault(symbol.parent, []).append(symbol)

    classes = {s.id: s for s in graph.symbols.values() if s.kind == "class"}

    def is_visitor(class_symbol: Symbol, seen: set[str] | None = None) -> bool:
        seen = seen if seen is not None else set()
        if class_symbol.id in seen:
            return False  # guard against a cyclic base chain
        seen.add(class_symbol.id)
        for base in class_symbol.bases:
            tail = base.rsplit(".", 1)[-1]
            if tail in VISITOR_BASES:
                return True
            # An in-project intermediate class may be the one inheriting it.
            for candidate in classes.values():
                if candidate.name == tail and is_visitor(candidate, seen):
                    return True
        return False

    for class_symbol in classes.values():
        if not is_visitor(class_symbol):
            continue
        for member in members.get(class_symbol.id, ()):
            if member.name.startswith(VISITOR_PREFIX) or member.name == "generic_visit":
                member.mark_entry_point("visitor_dispatch")


def _mark_main_modules(graph: CodeGraph, root: Path) -> None:
    """Mark the module-scope node of any module with an `if __name__ == "__main__"` block."""
    from .analyzer import module_scope_id

    for symbol in list(graph.symbols.values()):
        if symbol.kind != "module_scope":
            continue
        path = root / symbol.file
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        if any(_is_main_guard(node) for node in tree.body):
            graph.symbols[module_scope_id(symbol.module)].mark_entry_point("main_module")


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.comparators) != 1:
        return False
    left, comparator = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(comparator, ast.Constant)
        and comparator.value == "__main__"
    )


def _mark_console_scripts(graph: CodeGraph, root: Path) -> None:
    """Mark the targets of `[project.scripts]` and `[project.gui-scripts]`."""
    path = root / "pyproject.toml"
    if not path.exists():
        return
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python 3.10
            import tomli as tomllib
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    project = data.get("project", {})
    targets: list[str] = []
    for table in ("scripts", "gui-scripts"):
        targets.extend((project.get(table) or {}).values())

    for target in targets:
        if not isinstance(target, str) or ":" not in target:
            continue
        module, _, attribute = target.partition(":")
        symbol = graph.symbols.get(f"{module}.{attribute}")
        if symbol is not None:
            symbol.mark_entry_point("console_script")


__all__ = [
    "detect",
    "is_abstract",
    "is_test_file",
    "mark_project_roots",
]
