"""LineageLens: a deterministic code graph for humans and coding agents.

Control flow, data flow and cross-service contracts across six languages, in
one SQLite graph. See ``lineagelens.core`` for the vocabulary,
``lineagelens.query`` for the primitives.
"""

from __future__ import annotations

from .core import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION", "__version__"]
__version__ = "0.2.0"
