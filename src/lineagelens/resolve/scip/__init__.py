"""SCIP index ingestion (#47).

SCIP is Sourcegraph's Code Intelligence Protocol: a protobuf index emitted by
`scip-java`, `scip-typescript`, `scip-python` and friends, carrying
compiler-verified symbol occurrences.
"""

from __future__ import annotations

from .reader import (
    DEFINITION_ROLE,
    ScipDocument,
    ScipIndex,
    ScipOccurrence,
    ScipParseError,
    read_index,
)

__all__ = [
    "DEFINITION_ROLE",
    "ScipDocument",
    "ScipIndex",
    "ScipOccurrence",
    "ScipParseError",
    "read_index",
]
