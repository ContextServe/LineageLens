"""Safe, configurable static Python lineage analysis with Jedi-backed call resolution.

This module intentionally parses source and never imports the target project.
Uses Jedi for call resolution to correctly handle dynamic instance-method calls.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


# Binding provenances established by a literal annotation in the source, as opposed to
# inferred from a constructor call or a factory's declared return type.
_FACT_PROVENANCE = frozenset(
    {"annotated_attribute", "annotated_parameter", "annotated_assignment", "annotated_parameter_passthrough"}
)

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


@dataclass
class SymbolIndex:
    """Lookup tables over ``CodeGraph.symbols`` for mapping inference results back to ids.

    An inference engine reports a ``(file, line)`` pair alongside its dotted name, which
    is strictly more information than a name suffix. ``by_location`` uses it to
    disambiguate same-named symbols exactly; the name tables are fallbacks for when the
    reported line does not match our ``Symbol.line`` (decorated definitions, for
    instance, where the two disagree about where the symbol starts).

    ``by_suffix`` keys are dot-anchored by construction: for ``a.b.c.d`` the keys are
    ``d``, ``c.d``, ``b.c.d``, ``a.b.c.d``. A bare ``str.endswith`` test would let
    ``MyArchetypeEngine`` match ``ArchetypeEngine``; this cannot. Candidate lists are
    kept whole so an ambiguous name can be reported as ambiguous rather than silently
    resolved to whichever symbol happened to be inserted first.
    """

    by_location: dict[tuple[str, int], str] = field(default_factory=dict)
    by_file_name: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    by_suffix: dict[str, list[str]] = field(default_factory=dict)
    names: set[str] = field(default_factory=set)

    @classmethod
    def build(cls, graph: CodeGraph) -> SymbolIndex:
        index = cls()
        for symbol in graph.symbols.values():
            index.by_location.setdefault((symbol.file, symbol.line), symbol.id)
            index.by_file_name.setdefault((symbol.file, symbol.name), []).append(symbol.id)
            index.names.add(symbol.name)
            parts = symbol.id.split(".")
            for start in range(len(parts)):
                index.by_suffix.setdefault(".".join(parts[start:]), []).append(symbol.id)
        return index

    def resolve_target(
        self, graph: CodeGraph, relpath: str, line: int, full_name: str
    ) -> tuple[str | None, bool]:
        """Map an inferred target to a symbol id.

        Returns ``(symbol_id, ambiguous)``. ``ambiguous`` is True when two or more
        in-repo symbols match the name equally well, in which case the caller must
        decline to resolve rather than pick one.
        """
        if full_name in graph.symbols:
            return full_name, False

        located = self.by_location.get((relpath, line))
        if located:
            return located, False

        tail = full_name.rsplit(".", 1)[-1]
        for candidates in (self.by_file_name.get((relpath, tail)), self.by_suffix.get(full_name)):
            if not candidates:
                continue
            unique = set(candidates)
            if len(unique) == 1:
                return next(iter(unique)), False
            return None, True

        return None, False


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


def _iter_classes(node: ast.AST, prefix: str) -> Iterable[tuple[str, ast.ClassDef]]:
    """Yield ``(class_id, ClassDef)`` for every class under ``node``, nested ones included.

    Walks the body rather than using ``ast.walk`` so that the dotted id can be built up
    from the real lexical nesting; ``ast.walk`` flattens the tree and loses it.
    """
    for child in getattr(node, "body", []):
        if isinstance(child, ast.ClassDef):
            class_id = f"{prefix}.{child.name}"
            yield class_id, child
            yield from _iter_classes(child, class_id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _iter_classes(child, f"{prefix}.{child.name}")


def _annotated_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef, module: Module, graph: CodeGraph
) -> dict[str, str]:
    """Map parameter name -> class symbol id for every parameter with a class annotation."""
    params: dict[str, str] = {}
    for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
        bound = annotation_to_class(arg.annotation, module, graph)
        if bound:
            params[arg.arg] = bound
    return params


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
) -> dict[str, dict[str, tuple[str, str]]]:
    """Phase 1.5: map ``self.attr`` to a class symbol id for every class in every module.

    Returns ``{class_id: {attr_name: (class_symbol_id, provenance)}}``. The provenance
    is carried through to the relation's ``resolution_evidence`` so that a binding
    established by a literal annotation is not reported at the same trust tier as one
    guessed from a constructor call.

    Four binding forms are recognised, in precedence order:

    1. explicit annotation -- ``self.db: Database = ...``           (fact)
    2. constructor call -- ``self.db = Database()``                 (heuristic)
    3. annotated factory -- ``self.db = make_db()  # -> Database``  (heuristic)
    4. annotated-parameter passthrough --                           (fact)
       ``def __init__(self, db: Database): self.db = db``

    Form 4 is the most common shape in real code and is the one that lets
    ``self.db.query()`` resolve with no inference engine involved at all.
    """
    bindings: dict[str, dict[str, tuple[str, str]]] = {}
    for module in modules:
        try:
            for class_id, class_node in _iter_classes(module.tree, module.name):
                attr_map = bindings.setdefault(class_id, {})
                for stmt in class_node.body:
                    if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    params = _annotated_parameters(stmt, module, graph)
                    for inner_stmt in ast.walk(stmt):
                        target_name, annotation, rhs = _self_attr_assignment(inner_stmt)
                        if target_name is None:
                            continue
                        bound, provenance = None, "none"
                        if annotation is not None:
                            bound = annotation_to_class(annotation, module, graph)
                            provenance = "annotated_attribute"
                        if bound is None and rhs is not None:
                            bound, provenance = infer_bound_class(rhs, module, graph, module_registry)
                        if bound is None and isinstance(rhs, ast.Name):
                            bound = params.get(rhs.id)
                            provenance = "annotated_parameter_passthrough"
                        if bound:
                            attr_map[target_name] = (bound, provenance)
                        else:
                            attr_map.pop(target_name, None)
                if not attr_map:
                    bindings.pop(class_id, None)
        except Exception as e:
            report.failures.append(
                FileFailure(
                    file=str(module.path.relative_to(root)),
                    stage="attribute_hoisting",
                    error_type=type(e).__name__,
                    message=str(e),
                )
            )
    return bindings


class Definitions(ast.NodeVisitor):
    def __init__(self, module: Module, graph: CodeGraph, root: Path, report: AnalysisReport) -> None:
        self.module, self.graph, self.root, self.report = module, graph, root, report
        self.scope: list[str] = []
        self._module_container_created = False

    def identifier(self, name: str) -> str:
        return ".".join([self.module.name, *self.scope, name])

    def _parent_id(self) -> str:
        """The id of the lexically enclosing symbol, or the module container at top level."""
        return ".".join([self.module.name, *self.scope]) if self.scope else self.module.name

    def _register_child(self, symbol: Symbol) -> None:
        """Record a top-level symbol against its enclosing module container."""
        container = self.graph.containers.get(symbol.parent or "")
        if container is not None and symbol.id not in container.children:
            container.children.append(symbol.id)

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
                # `children` was declared and read but never written, so
                # get_module_overview().submodules was always empty.
                if parent and parent in self.graph.containers:
                    siblings = self.graph.containers[parent].children
                    if container_id not in siblings:
                        siblings.append(container_id)

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
        parent_id = self._parent_id()

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
        self._register_child(symbol)
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

        parent_id = self._parent_id()

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
        self._register_child(symbol)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


class Relationships(ast.NodeVisitor):
    def __init__(self, module: Module, graph: CodeGraph, root: Path, config: ProjectConfig, report: AnalysisReport,
                 jedi_resolver: JediResolver | None = None,
                 attribute_bindings: dict[str, dict[str, tuple[str, str]]] | None = None,
                 module_registry: dict[str, Module] | None = None,
                 index: SymbolIndex | None = None) -> None:
        self.module, self.graph, self.root, self.config, self.report = module, graph, root, config, report
        self.jedi_resolver = jedi_resolver
        self.attribute_bindings = attribute_bindings or {}
        self.module_registry = module_registry or {}
        self.index = index if index is not None else SymbolIndex.build(graph)
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

    @staticmethod
    def _jedi_position(node_func: ast.AST) -> tuple[int, int]:
        """The position to point the inference engine at for a call's ``func`` expression.

        For an ``Attribute`` the name we care about is the trailing ``.attr``, which sits
        at the *end* of the expression -- so the line must come from ``end_lineno``, not
        ``lineno``. Pairing the start line with an end-derived column lands on the wrong
        line for any multi-line attribute chain, e.g.::

            result = (some_object
                      .apply_migrations())
        """
        if isinstance(node_func, ast.Attribute) and node_func.end_col_offset is not None:
            line = node_func.end_lineno or node_func.lineno
            return line, max(node_func.end_col_offset - len(node_func.attr), 0)
        return node_func.lineno, node_func.col_offset

    def _resolve_via_jedi(self, raw: str, node_func: ast.AST) -> tuple[str, str, str] | None:
        """Resolve a call via the inference engine, or return None to fall through."""
        try:
            line, column = self._jedi_position(node_func)
            for target in self.jedi_resolver.resolve_call(self.module.path, line, column):
                if target.module_path is None or not target.full_name:
                    continue
                try:
                    relpath = str(target.module_path.relative_to(self.root))
                except ValueError:
                    continue  # outside the project: stdlib or a third-party package
                symbol_id, ambiguous = self.index.resolve_target(
                    self.graph, relpath, target.line, target.full_name
                )
                if symbol_id:
                    logger.debug("jedi resolved %s -> %s", raw, symbol_id)
                    return symbol_id, "resolved_via_inference", "jedi_inference"
                if ambiguous:
                    # Several in-repo symbols match this name equally well. Guessing by
                    # dict order would silently attribute the edge to the wrong symbol,
                    # which is worse than admitting we do not know.
                    logger.debug("jedi ambiguous for %s (%s)", raw, target.full_name)
                    return f"external:{raw}", "external_or_dynamic", "jedi_ambiguous"
        except Exception as e:
            logger.debug("jedi resolution failed for %s at %s: %s", raw, self.module.path, e)
        return None

    def resolve(self, raw: str | None, node_func: ast.AST | None = None) -> tuple[str, str, str]:
        """Resolve a call target; return ``(target_id, resolution, evidence_label)``.

        Cheap static resolution runs first and the inference engine is only consulted
        when it fails. The ordering is not merely a performance choice: whatever
        resolves a call also decides the relation's trust tier, and a scope walk over
        literal AST is a ``deterministic_fact`` where an inference result is only a
        ``deterministic_heuristic``. Asking the engine first therefore demotes edges we
        can prove, which would in turn understate reachability confidence downstream.

        Args:
            raw: The dotted name string (e.g. ``"conn.apply_migrations"``)
            node_func: The AST node being called, needed for an engine lookup
        """
        if not raw:
            return "external:<unresolved>", "external_or_dynamic", "unresolved_dynamic_dispatch"

        static = self._resolve_static(raw)
        if static[1] != "external_or_dynamic":
            return static

        if self.jedi_resolver and node_func:
            inferred = self._resolve_via_jedi(raw, node_func)
            if inferred:
                return inferred

        return static

    def _resolve_static(self, raw: str) -> tuple[str, str, str]:
        """Resolve a dotted name using only literal AST evidence in scope."""
        head, *tail = raw.split(".")

        if head in {"self", "cls"} and self.classes:
            current_class_id = self.class_ids[-1]
            if len(tail) >= 2:
                attr_name, *rest = tail
                bound = self.attribute_bindings.get(current_class_id, {}).get(attr_name)
                if bound:
                    bound_id, provenance = bound
                    resolution = "resolved" if provenance in _FACT_PROVENANCE else "resolved_via_inference"
                    direct = ".".join([bound_id, *rest])
                    if direct in self.graph.symbols:
                        return direct, resolution, provenance
                    via_base = self._resolve_via_bases(bound_id, rest)
                    if via_base:
                        return via_base, resolution, provenance
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
            # Deliberately fall through. An import table entry does not preclude a
            # richer local binding for the same name -- a module that does both
            # `from db import session` and `session = Session()` must still resolve
            # `session.query()` through the local binding below.

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
            # `contract.inputs` includes the receiver, so for a bound call like
            # `obj.method(a)` a naive zip maps `a` onto the parameter named `self`.
            # Known residual: an explicit unbound call, `Cls.method(inst, a)`, is also
            # an Attribute and so is still shifted by one. That form is rare enough to
            # leave; fixing it needs to know whether the attribute head names a class.
            if (
                contract
                and contract.kind == "method"
                and parameters
                and parameters[0]["name"] in ("self", "cls")
                and isinstance(node.func, ast.Attribute)
            ):
                parameters = parameters[1:]
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

    started_at = datetime.now(timezone.utc).isoformat()
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
    index = SymbolIndex.build(graph)

    # Phase 2: Analyze relationships (calls, risks, entry points)
    for item in modules:
        try:
            Relationships(
                item, graph, root, config, report, jedi_resolver, attribute_bindings, module_registry, index
            ).visit(item.tree)
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
    report.finished_at = datetime.now(timezone.utc).isoformat()

    return graph, report
