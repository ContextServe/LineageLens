"""Shared pytest configuration.

``pythonpath = ["src"]`` in ``pyproject.toml`` is the primary mechanism that
makes ``import lineagelens`` resolve to the working tree. This is the
belt-and-braces version for anyone invoking pytest from an unusual cwd, plus a
guard against a stale installed wheel shadowing ``src/`` -- which silently
tests the wrong code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _assert_working_tree_is_under_test() -> None:
    """Fail loudly if an installed copy shadows the working tree."""
    import lineagelens

    loaded = Path(lineagelens.__file__).resolve()
    try:
        loaded.relative_to(SRC_ROOT)
    except ValueError:  # pragma: no cover - only on a misconfigured env
        pytest.fail(
            f"lineagelens imported from {loaded}, not {SRC_ROOT}. "
            f"An installed wheel is shadowing the working tree; "
            f"run `pip install -e .` or unset PYTHONPATH."
        )
