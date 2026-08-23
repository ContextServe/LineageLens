"""Shared pytest configuration for the LineageLens test suite.

``pythonpath = ["src"]`` in ``pyproject.toml`` is the primary mechanism that
makes ``import lineagelens`` resolve to the working tree. This module is the
belt-and-braces version for anyone invoking pytest from an unusual cwd, and it
exposes the fixture-corpus paths that the golden reachability tests use.

See ``test_import_hygiene.py`` for the guard that fails loudly when a stale
installed wheel shadows ``src/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_root() -> Path:
    """Absolute path to ``tests/fixtures``."""
    return FIXTURES


@pytest.fixture(scope="session")
def corpus_root() -> Path:
    """Absolute path to the golden reachability corpus project root."""
    return FIXTURES / "reachability_corpus"
