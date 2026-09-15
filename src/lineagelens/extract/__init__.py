"""Tier A extraction: one engine, per-language declarative specs.

Everything language-specific lives in ``spec/<lang>/*.scm``. This package holds
the engine that consumes them, the grammar registry, and per-file language
detection.
"""

from __future__ import annotations

from .engine import EXTRACTOR_NAME, EXTRACTOR_VERSION, SpecExtractor
from .langs import (
    EXTENSIONS,
    GRAMMARS,
    MissingGrammar,
    ParserRegistry,
    detect_dialect,
    grammar_version,
    language_of,
    supported_languages,
)
from .spec import LanguageSpec, SpecError, SpecRegistry

__all__ = [
    "EXTENSIONS",
    "EXTRACTOR_NAME",
    "EXTRACTOR_VERSION",
    "GRAMMARS",
    "LanguageSpec",
    "MissingGrammar",
    "ParserRegistry",
    "SpecError",
    "SpecExtractor",
    "SpecRegistry",
    "detect_dialect",
    "grammar_version",
    "language_of",
    "supported_languages",
]
