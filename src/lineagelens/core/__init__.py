"""Core vocabulary and record types shared by every layer.

This package holds no logic beyond identity and normalisation. Extraction,
resolution, storage and query all depend on it; it depends on nothing else in
the codebase, so there is exactly one definition of what a node, an edge, and a
span are.
"""

from __future__ import annotations

from .ids import (
    anonymous_member,
    contract_id,
    file_node_id,
    node_id,
    normalise_module_path,
    normalise_type,
    qualified_name,
    service_id,
    signature_hash,
)
from .kinds import (
    CALL_CHAIN_EDGES,
    CONTRACT_EDGES,
    CONTROL_EDGES,
    DATA_EDGES,
    PERSISTED_EDGES,
    STRUCTURAL_EDGES,
    TEST_EDGES,
    BoundaryKind,
    ContractKind,
    DataflowMode,
    DataflowStatus,
    EdgeKind,
    EvidenceTier,
    Intent,
    NodeFlags,
    NodeKind,
    ParseStatus,
    RefKind,
    RefStatus,
    Resolution,
    SkipReason,
    Tier,
    Visibility,
)
from .span import Span
from .types import (
    Boundary,
    Contract,
    Coverage,
    Edge,
    Evidence,
    ExtractorRun,
    FileRecord,
    Node,
    Observation,
    ParseError,
    ResolvedTarget,
    Service,
    UnresolvedRef,
    dumps,
    loads,
)

#: Bumped whenever the on-disk shape changes. Schema 4 is a clean break from 3:
#: there is no dual-read path, and an older index is rejected rather than
#: silently upgraded (issue #51 §17.2). Schema 3's habit of preferring a lossy
#: store over a complete one, with only a debug-level log line to say so, is the
#: specific failure this refusal exists to prevent.
SCHEMA_VERSION = 4

__all__ = [
    "CALL_CHAIN_EDGES",
    "CONTRACT_EDGES",
    "CONTROL_EDGES",
    "DATA_EDGES",
    "PERSISTED_EDGES",
    "SCHEMA_VERSION",
    "STRUCTURAL_EDGES",
    "TEST_EDGES",
    "Boundary",
    "BoundaryKind",
    "Contract",
    "ContractKind",
    "Coverage",
    "DataflowMode",
    "DataflowStatus",
    "Edge",
    "EdgeKind",
    "Evidence",
    "EvidenceTier",
    "ExtractorRun",
    "FileRecord",
    "Intent",
    "Node",
    "NodeFlags",
    "NodeKind",
    "Observation",
    "ParseError",
    "ParseStatus",
    "RefKind",
    "RefStatus",
    "Resolution",
    "ResolvedTarget",
    "Service",
    "SkipReason",
    "Span",
    "Tier",
    "UnresolvedRef",
    "Visibility",
    "anonymous_member",
    "contract_id",
    "dumps",
    "file_node_id",
    "loads",
    "node_id",
    "normalise_module_path",
    "normalise_type",
    "qualified_name",
    "service_id",
    "signature_hash",
]
