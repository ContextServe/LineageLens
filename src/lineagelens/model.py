"""Canonical graph model shared by CLI, UI, GraphQL, and LLM enrichment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Symbol:
    id: str
    kind: str
    name: str
    file: str
    line: int
    module: str
    async_: bool = False
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    entry_point: str | None = None
    risks: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Relation:
    source: str
    target: str
    kind: str
    file: str
    line: int
    evidence: str = "static_ast"
    resolution: str = "resolved"
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CodeGraph:
    project_root: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)

    def add_symbol(self, symbol: Symbol) -> None:
        self.symbols[symbol.id] = symbol

    def add_relation(self, relation: Relation) -> None:
        self.relations.append(relation)

    def to_dict(self) -> dict[str, Any]:
        return {"symbols": [asdict(item) for item in self.symbols.values()], "relations": [asdict(item) for item in self.relations]}
