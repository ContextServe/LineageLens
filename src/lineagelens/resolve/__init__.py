"""Resolution. The only layer permitted to create edges.

Extractors emit observations; this package decides what they point at. The rule
that matters: when several candidates match, record the ambiguity and emit no
edge. An ambiguity is a useful answer, a wrong edge is not.
"""

from __future__ import annotations

from .index import SymbolIndex
from .oracles import (
    JediOracle,
    OracleAvailability,
    OracleRegistry,
    ResolverOracle,
    ToolchainOracle,
    default_oracles,
)
from .resolver import Resolver, ResolveResult, mark_entry_points

__all__ = [
    "JediOracle",
    "OracleAvailability",
    "OracleRegistry",
    "ResolveResult",
    "Resolver",
    "ResolverOracle",
    "SymbolIndex",
    "ToolchainOracle",
    "default_oracles",
    "mark_entry_points",
]
