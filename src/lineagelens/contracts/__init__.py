"""Cross-framework and cross-service contracts (issue #51 §8).

Every cross-framework hop has one shape: a producer declares a string key in a
shared namespace, and a consumer declares consumption of the same key. Modelling
the key as a *node* -- rather than drawing a direct producer-to-consumer edge --
buys four things: the join key stays visible in an answer, many-to-many falls
out naturally, the heuristic lives on its own edges and can be filtered
independently, and a contract with consumers but no exposer becomes a finding
rather than an invisible gap.
"""

from __future__ import annotations

from .adapters import (
    Adapter,
    AdapterError,
    AdapterRegistry,
    ContractMatch,
    project_adapter_roots,
)
from .detect import (
    ContractResult,
    UnclaimedDeclaration,
    detect_in_observation,
    detect_spi_resources,
)
from .normalise import (
    PLACEHOLDER,
    NormalisedKey,
    normalise,
    normalise_fqn,
    normalise_http,
    normalise_plain,
    normalise_table,
    normalise_topic,
)

__all__ = [
    "PLACEHOLDER",
    "Adapter",
    "AdapterError",
    "AdapterRegistry",
    "ContractMatch",
    "ContractResult",
    "NormalisedKey",
    "UnclaimedDeclaration",
    "detect_in_observation",
    "detect_spi_resources",
    "normalise",
    "normalise_fqn",
    "normalise_http",
    "normalise_plain",
    "normalise_table",
    "normalise_topic",
    "project_adapter_roots",
]
