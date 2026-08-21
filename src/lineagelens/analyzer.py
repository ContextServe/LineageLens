"""Safe, configurable static Python lineage analysis.

This module intentionally parses source and never imports the target project.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ProjectConfig
from .model import CodeGraph, Container, Evidence, Relation, ResiliencySignal, Symbol
from .report import AnalysisReport, FileFailure, SymbolWarning

logger = logging.getLogger(__name__)


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func)
    return None


def expression(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (ValueError, TypeError, AttributeError):
        return type(node).__name__


def infer(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    if isinstance(node, ast.List):
        return "list"
    if isinstance(node, ast.Dict):
        return "dict"
    if isinstance(node, ast.Set):
        return "set"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, ast.Call):
        return dotted(node.func) or "call result"
    return "unknown"


@dataclass
class Module:
    path: Path
    name: str
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)


class Definitions(ast.NodeVisitor):
    def __init__(self, module: Module, graph: CodeGraph, root: Path, report: AnalysisReport) -> None:
        self.module, self.graph, self.root, self.report = module, graph, root, report
        self.scope: list[str] = []
        self._module_container_created = False

    def identifier(self, name: str) -> str:
        return ".".join([self.module.name, *self.scope, name])

    def _ensure_module_container(self) -> None:
        """Create a container for the module if not already done."""
        if self._module_container_created or not self.module.name:
            return
        self._module_container_created = True

        # Create containers for all parent packages and the module itself
        parts = self.module.name.split(".")
        for i in range(1, len(parts) + 1):
            container_id = ".".join(parts[:i])
            if container_id not in self.graph.containers:
                is_module = i == len(parts)
                kind = "module" if is_module else "package"
                parent = ".".join(parts[:i-1]) if i > 1 else None
                self.graph.add_container(Container(
                    id=container_id,
                    kind=kind,
                    name=parts[i-1],
                    file=str(self.module.path.relative_to(self.root)) if is_module else None,
                    parent=parent,
                ))

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.module.imports[item.asname or item.name.split(".")[0]] = item.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # Handle relative imports safely
        if node.level > 0:
            module_parts = self.module.name.split(".")
            if node.level > len(module_parts):
                # Relative import goes above package root — skip
                return
            prefix = module_parts[:-node.level]
        else:
            prefix = []

        if node.module:
            base = ".".join([*prefix, *node.module.split(".")]).strip(".")
        else:
            base = ".".join(prefix).strip(".")

        for item in node.names:
            if item.name != "*":
                full_name = f"{base}.{item.name}" if base else item.name
                self.module.imports[item.asname or item.name] = full_name

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._ensure_module_container()
        parent_id = self.scope[-1] if self.scope else None
        if parent_id and len(self.scope) == 1:  # Parent is a class
            parent_id = ".".join([self.module.name, parent_id])
        elif parent_id:  # Nested class
            parent_id = ".".join([self.module.name, *self.scope])

        symbol = Symbol(
            id=self.identifier(node.name),
            kind="class",
            name=node.name,
            file=str(self.module.path.relative_to(self.root)),
            line=node.lineno,
            end_line=node.end_lineno,
            module=self.module.name,
            parent=parent_id,
            description=ast.get_docstring(node),
            decorators=[dotted(item) or expression(item) for item in node.decorator_list],
        )
        self.graph.add_symbol(symbol)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._ensure_module_container()
        inputs = []
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
        for arg, default in zip(args, defaults):
            inputs.append({"name": arg.arg, "type": expression(arg.annotation) if arg.annotation else "unknown", "default": expression(default) if default else None})

        # Determine parent: if directly in a class, parent is the class id; else parent is module id
        if self.scope and self.scope[-1][:1].isupper():  # Heuristic: class names start with uppercase
            parent_id = ".".join([self.module.name, *self.scope])
        elif self.scope:
            parent_id = ".".join([self.module.name, *self.scope])
        else:
            parent_id = self.module.name  # Parent is the module container

        symbol = Symbol(
            id=self.identifier(node.name),
            kind="method" if self.scope and self.scope[-1][:1].isupper() else "function",
            name=node.name,
            file=str(self.module.path.relative_to(self.root)),
            line=node.lineno,
            end_line=node.end_lineno,
            module=self.module.name,
            parent=parent_id,
            async_=isinstance(node, ast.AsyncFunctionDef),
            description=ast.get_docstring(node),
            inputs=inputs,
            outputs=[{"type": expression(node.returns) if node.returns else "unknown", "evidence": "annotation"}],
            decorators=[dotted(item) or expression(item) for item in node.decorator_list],
        )
        self.graph.add_symbol(symbol)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


class Relationships(ast.NodeVisitor):
    def __init__(self, module: Module, graph: CodeGraph, root: Path, config: ProjectConfig, report: AnalysisReport) -> None:
        self.module, self.graph, self.root, self.config, self.report = module, graph, root, config, report
        self.scope: list[str] = []
        self.classes: list[str] = []
        self.current: str | None = None
        self.awaited = False

    def identifier(self, name: str) -> str:
        return ".".join([self.module.name, *self.scope, name])

    def resolve(self, raw: str | None) -> tuple[str, bool]:
        if not raw:
            return "external:<unresolved>", False
        head, *tail = raw.split(".")
        if head in {"self", "cls"} and self.classes:
            candidate = ".".join([self.module.name, self.classes[-1], *tail])
            return candidate, candidate in self.graph.symbols
        if head in self.module.imports:
            return ".".join([self.module.imports[head], *tail]), False
        for depth in range(len(self.scope), -1, -1):
            candidate = ".".join([self.module.name, *self.scope[:depth], raw])
            if candidate in self.graph.symbols:
                return candidate, True
        return f"external:{raw}", False

    def mark_entry(self, node: ast.FunctionDef | ast.AsyncFunctionDef, symbol: Symbol) -> None:
        entries = self.config.analysis.entry_points
        decorators = [dotted(item) or "" for item in node.decorator_list]
        for item in decorators:
            if not item:
                continue
            if "fastapi" in self.config.frameworks and any(item.endswith(suffix) for suffix in entries.get("api_route", ())):
                symbol.entry_point = "api_route"
            if "typer" in self.config.frameworks and any(item.endswith(suffix) for suffix in entries.get("cli_command", ())):
                symbol.entry_point = "cli_command"
            if any(item.endswith(suffix) for suffix in entries.get("framework_callback", ())):
                symbol.entry_point = "framework_callback"
        if any(self.module.path.is_relative_to(self.root / part) for part in self.config.test_roots) and node.name.startswith("test_"):
            symbol.entry_point = "test"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.current
        self.current = self.identifier(node.name)

        # GUARD: only access symbol if it was created by Definitions visitor
        symbol = self.graph.symbols.get(self.current)
        if symbol:
            self.mark_entry(node, symbol)
        else:
            # Symbol not found — likely a visitor divergence, record warning
            self.report.warnings.append(
                SymbolWarning(
                    symbol_id=self.current,
                    file=str(self.module.path.relative_to(self.root)),
                    line=node.lineno,
                    message="Symbol not found during relationships pass (visitor divergence)",
                    stage="relationships",
                )
            )

        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        self.current = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Await(self, node: ast.Await) -> None:
        prior = self.awaited
        self.awaited = True
        self.visit(node.value)
        self.awaited = prior

    def visit_Call(self, node: ast.Call) -> None:
        raw = dotted(node.func)
        if self.current:
            target, resolved = self.resolve(raw)
            contract = self.graph.symbols.get(target)
            parameters = contract.inputs if contract else []
            arguments = [
                {
                    "parameter": parameters[index]["name"] if index < len(parameters) else None,
                    "expression": expression(value),
                    "inferred_type": infer(value),
                }
                for index, value in enumerate(node.args)
            ]
            arguments += [
                {"parameter": keyword.arg, "expression": expression(keyword.value), "inferred_type": infer(keyword.value)}
                for keyword in node.keywords
                if keyword.arg
            ]
            kind = "AWAIT_CALLS" if self.awaited else "CALLS"
            if raw and raw.endswith(("create_task", "ensure_future")):
                kind = "CREATES_TASK"

            # Create relation with Evidence object
            evidence = Evidence(tier="deterministic_fact", label="static_ast")
            self.graph.add_relation(
                Relation(
                    source=self.current,
                    target=target,
                    kind=kind,
                    file=str(self.module.path.relative_to(self.root)),
                    line=node.lineno,
                    evidence=evidence,
                    resolution="resolved" if resolved else "external_or_dynamic",
                    arguments=arguments,
                )
            )

            # Check resiliency rules against current symbol
            current_symbol = self.graph.symbols.get(self.current)
            if current_symbol:
                for rule in self.config.analysis.risk_rules:
                    if rule.only_in_async and not current_symbol.async_:
                        continue
                    if raw and any(word.lower() in raw.lower() for word in rule.match_words):
                        # Create Evidence for this resiliency signal
                        evidence = Evidence(
                            tier="deterministic_heuristic",
                            label=rule.category,
                        )
                        current_symbol.resiliency.append(
                            ResiliencySignal(
                                category=rule.category,
                                severity=rule.severity,
                                evidence=evidence,
                                line=node.lineno,
                            )
                        )

        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if self.current and node.value is not None:
            current_symbol = self.graph.symbols.get(self.current)
            if current_symbol:
                current_symbol.outputs.append(
                    {"type": infer(node.value), "expression": expression(node.value), "evidence": "return_expression"}
                )
        self.generic_visit(node)


def iter_python(root: Path, paths: Iterable[str]) -> Iterable[Path]:
    for item in paths:
        directory = root / item
        if directory.exists():
            yield from sorted(directory.rglob("*.py"))


def analyze(root: Path, config: ProjectConfig | None = None) -> tuple[CodeGraph, AnalysisReport]:
    """Analyze a Python project and return (CodeGraph, AnalysisReport).

    Never aborts on error. All per-file failures are recorded in the report
    and analysis continues with the next file.

    Args:
        root: Project root directory
        config: ProjectConfig, loaded from root if not provided

    Returns:
        (CodeGraph, AnalysisReport) tuple
    """
    root = root.resolve()
    config = config or ProjectConfig.load(root)
    graph = CodeGraph(str(root))
    modules: list[Module] = []

    # Initialize report
    started_at = datetime.utcnow().isoformat()
    report = AnalysisReport(
        project_root=str(root),
        started_at=started_at,
        finished_at="",
        files_scanned=0,
        files_skipped=0,
        symbols_found=0,
        relations_found=0,
        containers_found=0,
    )

    # Phase 1: Parse files and collect definitions
    for path in iter_python(root, [*config.source_roots, *config.test_roots, *config.script_roots]):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            report.files_scanned += 1
        except OSError as e:
            report.failures.append(FileFailure(file=str(path.relative_to(root)), stage="read", error_type=type(e).__name__, message=str(e)))
            report.files_skipped += 1
            continue
        except SyntaxError as e:
            report.failures.append(
                FileFailure(
                    file=str(path.relative_to(root)),
                    stage="parse",
                    error_type="SyntaxError",
                    message=str(e),
                    line=e.lineno,
                )
            )
            report.files_skipped += 1
            continue
        except UnicodeDecodeError as e:
            report.failures.append(FileFailure(file=str(path.relative_to(root)), stage="read", error_type="UnicodeDecodeError", message=str(e)))
            report.files_skipped += 1
            continue
        except Exception as e:
            report.failures.append(FileFailure(file=str(path.relative_to(root)), stage="parse", error_type=type(e).__name__, message=str(e)))
            report.files_skipped += 1
            continue

        # Compute module name
        package_path = path.relative_to(root).with_suffix("").parts
        # Preserve subpackage structure; only strip "src" build-root marker
        if package_path and package_path[-1] == "__init__":
            module = ".".join(part for part in package_path[:-1] if part != "src")
        else:
            module = ".".join(part for part in package_path if part != "src")

        if not module:  # Skip if module name is empty (edge case)
            report.files_skipped += 1
            continue

        # Run Definitions visitor
        try:
            item = Module(path, module, tree)
            Definitions(item, graph, root, report).visit(tree)
            modules.append(item)
        except Exception as e:
            report.failures.append(
                FileFailure(
                    file=str(path.relative_to(root)),
                    stage="definitions",
                    error_type=type(e).__name__,
                    message=str(e),
                )
            )
            continue

    # Phase 2: Analyze relationships (calls, risks, entry points)
    for item in modules:
        try:
            Relationships(item, graph, root, config, report).visit(item.tree)
        except Exception as e:
            report.failures.append(
                FileFailure(
                    file=str(item.path.relative_to(root)),
                    stage="relationships",
                    error_type=type(e).__name__,
                    message=str(e),
                )
            )
            continue

    # Update report with final counts
    report.symbols_found = len(graph.symbols)
    report.relations_found = len(graph.relations)
    report.containers_found = len(graph.containers)
    report.finished_at = datetime.utcnow().isoformat()

    return graph, report
