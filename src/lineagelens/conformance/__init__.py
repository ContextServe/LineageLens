"""The conformance suite behind the capability matrix (issue #51 §12).

One corpus per language, run through the real extractor and resolver. The
matrix it writes is a test artifact, so it cannot drift from behaviour the way
hand-written prose did.
"""

from __future__ import annotations

from .runner import (
    CORPUS_ROOT,
    EXPECTATIONS,
    MATRIX_PATH,
    Expectation,
    LanguageResult,
    run_all,
    run_language,
    write_matrix,
)

__all__ = [
    "CORPUS_ROOT",
    "EXPECTATIONS",
    "MATRIX_PATH",
    "Expectation",
    "LanguageResult",
    "run_all",
    "run_language",
    "write_matrix",
]
