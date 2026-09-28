"""Persistence. The single source of truth for a graph.

Schema 3 kept two stores of differing fidelity -- ``index.sqlite`` and
``graph.json`` -- and a loader that preferred the lossy one with only a
debug-level log line when it did. This package is the only writer, so that class
of divergence has nowhere to live.
"""

from __future__ import annotations

from .db import DB_FILENAME, GraphNotFound, GraphStore, SchemaMismatch
from .metrics_store import METRICS_DB_FILENAME, MetricRecord, MetricsStore
from .schema import RECURSION_LIMIT, SCHEMA_DDL

__all__ = [
    "DB_FILENAME",
    "METRICS_DB_FILENAME",
    "RECURSION_LIMIT",
    "SCHEMA_DDL",
    "GraphNotFound",
    "GraphStore",
    "MetricRecord",
    "MetricsStore",
    "SchemaMismatch",
]
