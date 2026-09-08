"""Query primitives (issue #51 §10).

Twelve primitives over the store, all sharing intent (§10.1), budget (§10.5)
and the completeness envelope (§10.6). ``get_lineage`` is gone: it returned a
reachability set with no predecessor links, so no chain was reconstructible and
its depths were DFS artefacts rather than distances.
"""

from __future__ import annotations

from .analysis import Analyser, ChangeKind, ImpactReport
from .api import QueryEngine
from .budget import (
    DEFAULT_DEPTH,
    DEFAULT_LIMIT,
    DEFAULT_MAX_PATHS,
    Budget,
    Envelope,
    QueryResult,
)
from .traverse import Hop, Path, Traverser, kinds_for_intent, source_for

__all__ = [
    "DEFAULT_DEPTH",
    "DEFAULT_LIMIT",
    "DEFAULT_MAX_PATHS",
    "Analyser",
    "Budget",
    "ChangeKind",
    "Envelope",
    "Hop",
    "ImpactReport",
    "Path",
    "QueryEngine",
    "QueryResult",
    "Traverser",
    "kinds_for_intent",
    "source_for",
]
