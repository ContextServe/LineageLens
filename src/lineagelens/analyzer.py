"""Safe, configurable static Python lineage analysis.

This module intentionally parses source and never imports the target project.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .config import ProjectConfig
from .model import CodeGraph, Relation, Symbol


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
    except Exception:
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
    def __init__(self, module: Module, graph: CodeGraph, root: Path) -> None:
        self.module, self.graph, self.root = module, graph, root
        self.scope: list[str] = []

    def identifier(self, name: str) -> str:
        return ".".join([self.module.name, *self.scope, name])

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self.module.imports[item.asname or item.name.split(".")[0]] = item.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = self.module.name.split(".")[:-node.level] if node.level else []
        base = ".".join([*prefix, node.module or ""]).strip(".")
        for item in node.names:
            if item.name != "*":
                self.module.imports[item.asname or item.name] = f"{base}.{item.name}".strip(".")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.graph.add_symbol(Symbol(self.identifier(node.name), "class", node.name, str(self.module.path.relative_to(self.root)), node.lineno, self.module.name,
                                     decorators=[dotted(item) or expression(item) for item in node.decorator_list]))
        self.scope.append(node.name); self.generic_visit(node); self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        inputs = []
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
        for arg, default in zip(args, defaults):
            inputs.append({"name": arg.arg, "type": expression(arg.annotation) if arg.annotation else "unknown", "default": expression(default) if default else None})
        self.graph.add_symbol(Symbol(self.identifier(node.name), "method" if self.scope and self.scope[-1][:1].isupper() else "function", node.name,
                                     str(self.module.path.relative_to(self.root)), node.lineno, self.module.name,
                                     async_=isinstance(node, ast.AsyncFunctionDef), inputs=inputs,
                                     outputs=[{"type": expression(node.returns) if node.returns else "unknown", "evidence": "annotation"}],
                                     decorators=[dotted(item) or expression(item) for item in node.decorator_list]))
        self.scope.append(node.name); self.generic_visit(node); self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


class Relationships(ast.NodeVisitor):
    def __init__(self, module: Module, graph: CodeGraph, root: Path, config: ProjectConfig) -> None:
        self.module, self.graph, self.root, self.config = module, graph, root, config
        self.scope: list[str] = []; self.classes: list[str] = []; self.current: str | None = None; self.awaited = False

    def identifier(self, name: str) -> str:
        return ".".join([self.module.name, *self.scope, name])

    def resolve(self, raw: str | None) -> tuple[str, bool]:
        if not raw: return "external:<unresolved>", False
        head, *tail = raw.split(".")
        if head in {"self", "cls"} and self.classes:
            candidate = ".".join([self.module.name, self.classes[-1], *tail])
            return candidate, candidate in self.graph.symbols
        if head in self.module.imports:
            return ".".join([self.module.imports[head], *tail]), False
        for depth in range(len(self.scope), -1, -1):
            candidate = ".".join([self.module.name, *self.scope[:depth], raw])
            if candidate in self.graph.symbols: return candidate, True
        return f"external:{raw}", False

    def mark_entry(self, node: ast.FunctionDef | ast.AsyncFunctionDef, symbol: Symbol) -> None:
        decorators = [dotted(item) or "" for item in node.decorator_list]
        for item in decorators:
            if "fastapi" in self.config.frameworks and re.search(r"\.(get|post|put|patch|delete|websocket)$", item): symbol.entry_point = "api_route"
            if "typer" in self.config.frameworks and item.endswith((".command", ".callback")): symbol.entry_point = "cli_command"
            if item.endswith((".middleware", ".exception_handler", ".on_event")): symbol.entry_point = "framework_callback"
            if item.endswith(("validator", "field_validator", "model_validator")): symbol.entry_point = "framework_callback"
        if any(self.module.path.is_relative_to(self.root / part) for part in self.config.test_roots) and node.name.startswith("test_"): symbol.entry_point = "test"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name); self.classes.append(node.name); self.generic_visit(node); self.classes.pop(); self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.current; self.current = self.identifier(node.name)
        self.mark_entry(node, self.graph.symbols[self.current])
        self.scope.append(node.name); self.generic_visit(node); self.scope.pop(); self.current = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Await(self, node: ast.Await) -> None:
        prior = self.awaited; self.awaited = True; self.visit(node.value); self.awaited = prior

    def visit_Call(self, node: ast.Call) -> None:
        raw = dotted(node.func)
        if self.current:
            target, resolved = self.resolve(raw)
            contract = self.graph.symbols.get(target)
            parameters = contract.inputs if contract else []
            arguments = [{"parameter": parameters[index]["name"] if index < len(parameters) else None, "expression": expression(value), "inferred_type": infer(value)} for index, value in enumerate(node.args)]
            arguments += [{"parameter": keyword.arg, "expression": expression(keyword.value), "inferred_type": infer(keyword.value)} for keyword in node.keywords if keyword.arg]
            kind = "AWAIT_CALLS" if self.awaited else "CALLS"
            if raw and raw.endswith(("create_task", "ensure_future")): kind = "CREATES_TASK"
            self.graph.add_relation(Relation(self.current, target, kind, str(self.module.path.relative_to(self.root)), node.lineno,
                                             resolution="resolved" if resolved else "external_or_dynamic", arguments=arguments))
            if raw and any(word in raw.lower() for word in ("execute", "insert", "update", "delete", "write", "save", "commit")):
                self.graph.symbols[self.current].risks.append({"category": "data_write", "severity": "review", "evidence": f"{raw} at line {node.lineno}"})
            if self.graph.symbols[self.current].async_ and raw and any(word in raw.lower() for word in ("requests.", "time.sleep", "subprocess.")):
                self.graph.symbols[self.current].risks.append({"category": "blocking_in_async", "severity": "high", "evidence": f"{raw} at line {node.lineno}"})
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if self.current and node.value is not None:
            self.graph.symbols[self.current].outputs.append({"type": infer(node.value), "expression": expression(node.value), "evidence": "return_expression"})
        self.generic_visit(node)


def iter_python(root: Path, paths: Iterable[str]) -> Iterable[Path]:
    for item in paths:
        directory = root / item
        if directory.exists(): yield from sorted(directory.rglob("*.py"))


def analyze(root: Path, config: ProjectConfig | None = None) -> CodeGraph:
    root = root.resolve(); config = config or ProjectConfig.load(root); graph = CodeGraph(str(root)); modules: list[Module] = []
    for path in iter_python(root, [*config.source_roots, *config.test_roots, *config.script_roots]):
        try: tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError): continue
        package_path = path.relative_to(root).with_suffix("").parts
        module = ".".join(part for part in package_path if part not in {"src", "__init__"})
        item = Module(path, module, tree); Definitions(item, graph, root).visit(tree); modules.append(item)
    for item in modules: Relationships(item, graph, root, config).visit(item.tree)
    return graph
