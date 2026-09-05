"""Canonical graph model shared by CLI, UI, GraphQL, and LLM enrichment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Bump whenever the on-disk shape or the meaning of symbol ids changes, so a
# stale graph.json is rejected outright instead of silently producing nonsense.
#
#   1  initial release
#   2  module ids strip only a leading source root (a nested "src" component is
#      no longer dropped); synthetic "<module>" symbols own module-level
#      statements; project_root and schema_version are persisted
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Evidence:
    """Evidence/trust tier for a signal in the code graph."""

    tier: Literal["deterministic_fact", "deterministic_heuristic", "probabilistic"]
    label: str
    confidence: float | None = None  # Meaningful only for probabilistic (0.0-1.0)

    @classmethod
    def from_legacy(cls, raw: str) -> Evidence:
        """Convert a legacy string label to typed Evidence (best-guess mapping)."""
        # Tier 1: deterministic facts -- read directly off literal AST
        if raw in ("static_ast", "annotation", "static_scope_walk", "annotated_parameter",
                   "annotated_assignment", "annotated_attribute", "annotated_parameter_passthrough",
                   "import_substitution",
                   # syntax observations for the non-call relation kinds
                   "static_ast_base", "static_ast_decorator", "static_ast_annotation",
                   "static_ast_dunder_all", "static_ast_import", "static_ast_name_load"):
            return cls(tier="deterministic_fact", label=raw)
        # Tier 2: deterministic heuristics -- reproducible, but inferred
        if raw in ("return_expression", "inferred_type", "resolved", "external_or_dynamic",
                   "local_type_inference_construction", "local_type_inference_factory",
                   "local_type_inference_attribute", "unresolved_dynamic_dispatch",
                   "jedi_inference", "jedi_ambiguous",
                   # name-based links: no syntax proves these, only a matching name
                   "mro_name_match", "pytest_fixture_name",
                   "string_literal_dotted_name", "string_reference"):
            return cls(tier="deterministic_heuristic", label=raw)
        # Tier 3: probabilistic
        if raw == "llm":
            return cls(tier="probabilistic", label=raw)
        # Unknown: treat as heuristic to be safe
        return cls(tier="deterministic_heuristic", label=raw)


@dataclass
class ResiliencySignal:
    """A resiliency/risk signal detected for a symbol (e.g., data write, blocking in async)."""

    category: str
    severity: str  # "info", "review", "high"
    evidence: Evidence
    line: int


@dataclass
class Container:
    """A package or module in the code hierarchy (intermediate node between CodeGraph and Symbol)."""

    id: str  # e.g., "myapp" (package), "myapp.utils" (module)
    kind: Literal["package", "module"]
    name: str  # e.g., "myapp" or "utils"
    file: str | None  # None for namespace packages with no __init__.py content
    parent: str | None = None  # id of enclosing package, or None for top-level
    children: list[str] = field(default_factory=list)  # direct child symbol/container ids
    docstring: str | None = None


@dataclass
class Symbol:
    id: str
    kind: str  # "class", "function", "method"
    name: str
    file: str
    line: int
    module: str
    parent: str | None = None  # id of enclosing module container or enclosing class symbol
    end_line: int | None = None  # end_lineno from AST (Python 3.8+)
    async_: bool = False
    description: str | None = None  # Docstring, tier deterministic_fact
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)  # Base class names (for classes only)
    # Every reason this symbol is an entry point. A single slot silently lost
    # information: mark_entry's sequential ifs overwrote each other, so a test_
    # function that also carried @router.get ended up as whichever rule ran last.
    entry_point_kinds: list[str] = field(default_factory=list)
    is_abstract: bool = False
    resiliency: list[ResiliencySignal] = field(default_factory=list)
    type_: str | None = None  # For fields: the type of the field
    visibility: str | None = None  # "public", "private", "protected", "package"
    static_: bool = False
    final_: bool = False

    @property
    def entry_point(self) -> str | None:
        """The primary entry-point kind, or None. Derived from entry_point_kinds."""
        return self.entry_point_kinds[0] if self.entry_point_kinds else None

    def mark_entry_point(self, kind: str) -> None:
        if kind not in self.entry_point_kinds:
            self.entry_point_kinds.append(kind)

    @property
    def risks(self) -> list[dict[str, Any]]:
        """Legacy property: return resiliency signals as dicts for backward compat."""
        return [{"category": r.category, "severity": r.severity, "evidence": r.evidence.label, "line": r.line} for r in self.resiliency]


@dataclass
class Relation:
    source: str
    target: str
    kind: str
    file: str
    line: int
    evidence: Evidence = field(default_factory=lambda: Evidence(tier="deterministic_fact", label="static_ast"))
    resolution: str = "resolved"  # One of: "resolved", "resolved_via_inference", "external_or_dynamic"
    resolution_evidence: Evidence = field(default_factory=lambda: Evidence(tier="deterministic_fact", label="static_scope_walk"))
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CodeGraph:
    project_root: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    containers: dict[str, Container] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)

    def add_symbol(self, symbol: Symbol) -> None:
        self.symbols[symbol.id] = symbol

    def add_container(self, container: Container) -> None:
        self.containers[container.id] = container

    def add_relation(self, relation: Relation) -> None:
        self.relations.append(relation)

    def to_dict(self) -> dict[str, Any]:
        # entry_point is a property, so asdict() does not include it. Emit it
        # explicitly: it is the field every consumer reads.
        symbols = []
        for symbol in self.symbols.values():
            payload = asdict(symbol)
            payload["entry_point"] = symbol.entry_point
            symbols.append(payload)
        return {
            "schema_version": SCHEMA_VERSION,
            "project_root": self.project_root,
            "symbols": symbols,
            "containers": [asdict(item) for item in self.containers.values()],
            "relations": [asdict(item) for item in self.relations],
        }
