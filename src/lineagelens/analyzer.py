"""Safe, configurable static Python lineage analysis with Jedi-backed call resolution.

This module intentionally parses source and never imports the target project.
Uses Jedi for call resolution to correctly handle dynamic instance-method calls.
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
from .resolve_jedi import JediResolver

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


_RESOLUTION_TIER = {
    "resolved": "deterministic_fact",
    "resolved_via_inference": "deterministic_heuristic",
    "external_or_dynamic": "deterministic_heuristic",
}


def _strip_quotes(text: str) -> str:
    """Undo ast.unparse's literal quoting of forward-ref string annotations."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


@dataclass
class Module:
    path: Path
    name: str
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)


def resolve_module_symbol(name: str | None, module: Module, graph: CodeGraph) -> Symbol | None:
    """Resolve a dotted name to a symbol using import-substitution or same-module match."""
    if not name:
        return None
    head, *tail = name.split(".")
    candidate = ".".join([module.imports[head], *tail]) if head in module.imports else ".".join([module.name, name])
    return graph.symbols.get(candidate)


def annotation_to_class(annotation: ast.AST | None, module: Module, graph: CodeGraph) -> str | None:
    """Resolve a parameter/AnnAssign annotation to a class Symbol id."""
    if annotation is None:
        return None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        name = annotation.value
    else:
        name = dotted(annotation)
    if not name:
        return None
    symbol = resolve_module_symbol(name, module, graph)
    return symbol.id if symbol and symbol.kind == "class" else None


def infer_bound_class(node: ast.AST, module: Module, graph: CodeGraph, module_registry: dict[str, Module]) -> tuple[str | None, str]:
    """Infer the bound class from an assignment RHS."""
    if not isinstance(node, ast.Call):
        return None, "none"
    raw = dotted(node.func)
    direct = resolve_module_symbol(raw, module, graph)
    if direct and direct.kind == "class":
        return direct.id, "local_type_inference_construction"
    if direct and direct.kind in ("function", "method") and direct.outputs:
        annotated_type = direct.outputs[0].get("type")
        if annotated_type and annotated_type != "unknown":
            defining_module = module_registry.get(direct.module, module)
            target = resolve_module_symbol(_strip_quotes(annotated_type), defining_module, graph)
            if target and target.kind == "class":
                return target.id, "local_type_inference_factory"
    return None, "none"


def _find_classes(tree: ast.AST, module_name: str, scope: list[str] | None = None) -> Iterable[tuple[str, ast.ClassDef]]:
    """Recursively yield (class_id, ClassDef) for all classes in tree."""
    if scope is None:
        scope = []
    for node in ast.walk(tree) if not scope else []:
        if isinstance(node, ast.ClassDef):
            class_id = ".".join([module_name, *scope, node.name])
            yield class_id, node


def _self_attr_assignment(stmt: ast.AST) -> tuple[str | None, ast.AST | None, ast.AST | None]:
    """Match self.attr assignment; return (attr_name, annotation, rhs) or (None, None, None)."""
    if isinstance(stmt, ast.Assign):
        if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Attribute):
            target = stmt.targets[0]
            if isinstance(target.value, ast.Name) and target.value.id == "self":
                return target.attr, None, stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        if isinstance(stmt.target, ast.Attribute):
            target = stmt.target
            if isinstance(target.value, ast.Name) and target.value.id == "self":
                return target.attr, stmt.annotation, stmt.value
    return None, None, None


def hoist_attribute_bindings(
    modules: list[Module], graph: CodeGraph, module_registry: dict[str, Module], report: AnalysisReport, root: Path
) -> dict[str, dict[str, str]]:
    """Phase 1.5: scan class methods for self.attr bindings."""
    bindings: dict[str, dict[str, str]] = {}
    for module in modules:
        try:
            class_bodies = {}
            for node in ast.walk(module.tree):
                if isinstance(node, ast.ClassDef):
                    class_id = ".".join([module.name] + [n.name for n in ast.walk(module.tree) if isinstance(n, ast.ClassDef) and node in list(ast.walk(module.tree))])
                    class_bodies[node] = []

            # Simplified: iterate through top-level and nested classes in module.tree
            for node in module.tree.body:
                if isinstance(node, ast.ClassDef):
                    class_id = ".".join([module.name, node.name])
                    attr_map = bindings.setdefault(class_id, {})
                    for stmt in node.body:
                        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            for inner_stmt in ast.walk(stmt):
                                target_name, annotation, rhs = _self_attr_assignment(inner_stmt)
                                if target_name is None:
                                    continue
                                bound = annotation_to_class(annotation, module, graph) if annotation is not None else None
                                if bound is None and rhs is not None:
                                    bound, _ = infer_bound_class(rhs, module, graph, module_registry)
                                if bound:
                                    attr_map[target_name] = bound
                                else:
                                    attr_map.pop(target_name, None)
                    if not attr_map:
                        bindings.pop(class_id, None)
        except Exception as e:
            report.failures.append(FileFailure(file=str(module.path.relative_to(root)), stage="attribute_hoisting", error_type=type(e).__name__, message=str(e)))
    return bindings


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
        if node.level > 0:
            module_parts = self.module.name.split(".")
            if node.level > len(module_parts):
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
        if parent_id and len(self.scope) == 1:
            parent_id = ".".join([self.module.name, parent_id])
        elif parent_id:
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
            bases=[dotted(item) or expression(item) for item in node.bases],
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

        if self.scope and self.scope[-1][:1].isupper():
            parent_id = ".".join([self.module.name, *self.scope])
        elif self.scope:
            parent_id = ".".join([self.module.name, *self.scope])
        else:
            parent_id = self.module.name

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
    def __init__(self, module: Module, graph: CodeGraph, root: Path, config: ProjectConfig, report: AnalysisReport,
                 jedi_resolver: JediResolver | None = None, attribute_bindings: dict[str, dict[str, str]] | None = None,
                 module_registry: dict[str, Module] | None = None) -> None:
        self.module, self.graph, self.root, self.config, self.report = module, graph, root, config, report
        self.jedi_resolver = jedi_resolver
        self.attribute_bindings = attribute_bindings or {}
        self.module_registry = module_registry or {}
        self.scope: list[str] = []
        self.classes: list[str] = []
        self.class_ids: list[str] = []
        self.bindings: list[dict[str, tuple[str, str]]] = [{}]
        self.current: str | None = None
        self.awaited = False

    def identifier(self, name: str) -> str:
        return ".".join([self.module.name, *self.scope, name])

    def _lookup_binding(self, name: str) -> tuple[str, str] | None:
        """Look up a variable name in the scope chain."""
        for scope in reversed(self.bindings):
            if name in scope:
                return scope[name]
        return None

    def _resolve_via_bases(self, class_id: str, tail: list[str]) -> str | None:
        """Walk direct base classes for a method."""
        if not tail:
            return None
        class_symbol = self.graph.symbols.get(class_id)
        if not class_symbol:
            return None
        defining_module = self.module_registry.get(class_symbol.module, self.module)
        for base_name in class_symbol.bases:
            base_symbol = resolve_module_symbol(base_name, defining_module, self.graph)
            if base_symbol and base_symbol.kind == "class":
                candidate = ".".join([base_symbol.id, *tail])
                if candidate in self.graph.symbols:
                    return candidate
        return None

    def resolve(self, raw: str | None, node_func: ast.AST | None = None) -> tuple[str, str, str]:
        """Resolve a call target; return (target_id, resolution, evidence_label).

        Args:
            raw: The dotted name string (e.g., "conn.apply_migrations")
            node_func: The AST node for the function being called (for Jedi lookup)
        """
        if not raw:
            return "external:<unresolved>", "external_or_dynamic", "unresolved_dynamic_dispatch"

        # Try Jedi first (if we have the AST node and a resolver)
        if self.jedi_resolver and node_func:
            try:
                # For Attribute nodes (e.g., conn.apply_migrations), point Jedi at the attribute name
                # For other nodes, use the node's own position
                if isinstance(node_func, ast.Attribute):
                    jedi_line = node_func.lineno
                    jedi_col = node_func.end_col_offset - len(node_func.attr) if node_func.end_col_offset else node_func.col_offset
                else:
                    jedi_line = node_func.lineno
                    jedi_col = node_func.col_offset

                jedi_results = self.jedi_resolver.resolve_call(self.module.path, jedi_line, jedi_col)
                for jedi_target in jedi_results:
                    # Check if this is an in-repo file
                    try:
                        if jedi_target.module_path.is_relative_to(self.root):
                            if not jedi_target.full_name:
                                continue

                            # Try exact match first
                            if jedi_target.full_name in self.graph.symbols:
                                logger.debug(f"Jedi resolved {raw} to {jedi_target.full_name}")
                                return jedi_target.full_name, "resolved_via_inference", "jedi_inference"

                            # Try matching by suffix (account for module path variations)
                            for sym_id in self.graph.symbols:
                                if sym_id.endswith("." + jedi_target.full_name) or sym_id.endswith(jedi_target.full_name):
                                    logger.debug(f"Jedi resolved {raw} to {sym_id} (suffix match on {jedi_target.full_name})")
                                    return sym_id, "resolved_via_inference", "jedi_inference"

                            # Special case: if Jedi returned a class but we're in a Call context,
                            # try finding the __init__ method of that class
                            if jedi_target.type == "class":
                                init_id = jedi_target.full_name + ".__init__"
                                for sym_id in self.graph.symbols:
                                    if sym_id.endswith("." + init_id) or sym_id.endswith(init_id):
                                        logger.debug(f"Jedi resolved {raw} to {sym_id} (constructor __init__)")
                                        return sym_id, "resolved_via_inference", "jedi_inference"
                    except (ValueError, AttributeError):
                        continue
            except Exception as e:
                logger.debug(f"Jedi resolution failed for {raw} at {self.module.path}:{node_func.lineno}: {e}")

        head, *tail = raw.split(".")

        if head in {"self", "cls"} and self.classes:
            current_class_id = self.class_ids[-1]
            if len(tail) >= 2:
                attr_name, *rest = tail
                bound = self.attribute_bindings.get(current_class_id, {}).get(attr_name)
                if bound:
                    direct = ".".join([bound, *rest])
                    if direct in self.graph.symbols:
                        return direct, "resolved_via_inference", "local_type_inference_attribute"
                    via_base = self._resolve_via_bases(bound, rest)
                    if via_base:
                        return via_base, "resolved_via_inference", "local_type_inference_attribute"
            candidate = ".".join([current_class_id, *tail])
            if candidate in self.graph.symbols:
                return candidate, "resolved", "static_scope_walk"
            via_base = self._resolve_via_bases(current_class_id, tail)
            if via_base:
                return via_base, "resolved", "static_scope_walk"
            return candidate, "external_or_dynamic", "unresolved_dynamic_dispatch"

        if head == "super" and self.classes:
            via_base = self._resolve_via_bases(self.class_ids[-1], tail)
            if via_base:
                return via_base, "resolved", "static_scope_walk"
            return f"external:{raw}", "external_or_dynamic", "unresolved_dynamic_dispatch"

        if head in self.module.imports:
            candidate = ".".join([self.module.imports[head], *tail])
            if candidate in self.graph.symbols:
                return candidate, "resolved", "static_scope_walk"
            return candidate, "external_or_dynamic", "unresolved_dynamic_dispatch"

        for depth in range(len(self.scope), -1, -1):
            candidate = ".".join([self.module.name, *self.scope[:depth], raw])
            if candidate in self.graph.symbols:
                return candidate, "resolved", "static_scope_walk"

        binding = self._lookup_binding(head)
        if binding and tail:
            bound_class, provenance = binding
            resolution = "resolved" if provenance in ("annotated_parameter", "annotated_assignment") else "resolved_via_inference"
            candidate = ".".join([bound_class, *tail])
            if candidate in self.graph.symbols:
                return candidate, resolution, provenance
            via_base = self._resolve_via_bases(bound_class, tail)
            if via_base:
                return via_base, resolution, provenance

        return f"external:{raw}", "external_or_dynamic", "unresolved_dynamic_dispatch"

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
        class_id = self.identifier(node.name)
        self.scope.append(node.name)
        self.classes.append(node.name)
        self.class_ids.append(class_id)
        self.generic_visit(node)
        self.class_ids.pop()
        self.classes.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        previous = self.current
        self.current = self.identifier(node.name)
        symbol = self.graph.symbols.get(self.current)
        if symbol:
            self.mark_entry(node, symbol)
        else:
            self.report.warnings.append(
                SymbolWarning(
                    symbol_id=self.current,
                    file=str(self.module.path.relative_to(self.root)),
                    line=node.lineno,
                    message="Symbol not found during relationships pass (visitor divergence)",
                    stage="relationships",
                )
            )

        self.bindings.append({})
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            if arg.annotation is not None:
                bound = annotation_to_class(arg.annotation, self.module, self.graph)
                if bound:
                    self.bindings[-1][arg.arg] = (bound, "annotated_parameter")

        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        self.bindings.pop()
        self.current = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and self.bindings:
            name = node.targets[0].id
            bound_class, provenance = infer_bound_class(node.value, self.module, self.graph, self.module_registry)
            if bound_class:
                self.bindings[-1][name] = (bound_class, provenance)
            else:
                self.bindings[-1].pop(name, None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if isinstance(node.target, ast.Name) and self.bindings:
            name = node.target.id
            bound_class = annotation_to_class(node.annotation, self.module, self.graph)
            if bound_class:
                self.bindings[-1][name] = (bound_class, "annotated_assignment")
            elif node.value is not None:
                inferred_class, provenance = infer_bound_class(node.value, self.module, self.graph, self.module_registry)
                if inferred_class:
                    self.bindings[-1][name] = (inferred_class, provenance)
                else:
                    self.bindings[-1].pop(name, None)
            else:
                self.bindings[-1].pop(name, None)

    def visit_Await(self, node: ast.Await) -> None:
        prior = self.awaited
        self.awaited = True
        self.visit(node.value)
        self.awaited = prior

    def visit_Call(self, node: ast.Call) -> None:
        raw = dotted(node.func)
        if self.current:
            target, resolution, evidence_label = self.resolve(raw, node_func=node.func)
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

            evidence = Evidence(tier="deterministic_fact", label="static_ast")
            resolution_evidence = Evidence(tier=_RESOLUTION_TIER[resolution], label=evidence_label)
            self.graph.add_relation(
                Relation(
                    source=self.current,
                    target=target,
                    kind=kind,
                    file=str(self.module.path.relative_to(self.root)),
                    line=node.lineno,
                    evidence=evidence,
                    resolution=resolution,
                    resolution_evidence=resolution_evidence,
                    arguments=arguments,
                )
            )

            current_symbol = self.graph.symbols.get(self.current)
            if current_symbol:
                for rule in self.config.analysis.risk_rules:
                    if rule.only_in_async and not current_symbol.async_:
                        continue
                    if raw and any(word.lower() in raw.lower() for word in rule.match_words):
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

        package_path = path.relative_to(root).with_suffix("").parts
        if package_path and package_path[-1] == "__init__":
            module = ".".join(part for part in package_path[:-1] if part != "src")
        else:
            module = ".".join(part for part in package_path if part != "src")

        if not module:
            report.files_skipped += 1
            continue

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

    # Phase 1.5: Build module registry and hoist self.attr bindings
    module_registry: dict[str, Module] = {m.name: m for m in modules}
    attribute_bindings = hoist_attribute_bindings(modules, graph, module_registry, report, root)
    jedi_resolver = JediResolver(root)

    # Phase 2: Analyze relationships (calls, risks, entry points)
    for item in modules:
        try:
            Relationships(item, graph, root, config, report, jedi_resolver, attribute_bindings, module_registry).visit(item.tree)
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

    report.symbols_found = len(graph.symbols)
    report.relations_found = len(graph.relations)
    report.containers_found = len(graph.containers)
    report.finished_at = datetime.utcnow().isoformat()

    return graph, report
