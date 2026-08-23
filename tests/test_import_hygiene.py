"""Guard against the working tree being shadowed by an installed copy.

This project was once installed non-editably from ``dist/*.whl``, so ``pytest``
imported ``site-packages/lineagelens`` rather than ``src/``. The two trees drifted
by ~30 lines and a green test run said nothing about the code under development.
That is how the type-inference work came to be declared complete three times
while the resolver was never invoked. Keep this test first in the suite.
"""

from __future__ import annotations

import json
from importlib.metadata import Distribution, PackageNotFoundError
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

REMEDIATION = (
    "conda activate lineagelens && "
    "pip uninstall -y lineagelens && "
    'pip install -e ".[dev,web,mcp]"'
)


def _install_kind() -> str:
    """How lineagelens is installed, for a more useful failure message.

    Only diagnostic. Building and installing a wheel is a legitimate thing to do
    while testing packaging, so it is not asserted against on its own -- what
    matters is whether the import below resolves to the working tree.
    """
    try:
        dist = Distribution.from_name("lineagelens")
    except PackageNotFoundError:
        return "not installed (pythonpath is doing the work)"

    raw = dist.read_text("direct_url.json")
    if not raw:
        return "installed from an index or an unknown source"
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return "installed, with unreadable direct_url.json"
    if "archive_info" in info:
        return f"installed NON-EDITABLY from {info.get('url')}"
    return f"installed editable from {info.get('url')}"


def test_lineagelens_imports_from_source_tree() -> None:
    """The one property that has to hold: tests exercise ``src/``, nothing else."""
    import lineagelens

    resolved = Path(lineagelens.__file__).resolve()
    assert resolved.is_relative_to(SRC_ROOT), (
        f"imported lineagelens from {resolved}\n"
        f"expected a path under {SRC_ROOT}\n"
        f"distribution is {_install_kind()}\n"
        f"An installed copy is shadowing the working tree. Fix with:\n  {REMEDIATION}"
    )


def test_pythonpath_is_configured() -> None:
    """``pythonpath = ["src"]`` is what makes the assertion above hold.

    Checked directly because it is easy to drop while editing pyproject, and its
    absence only shows up as a confusing pass against the wrong tree.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pythonpath = ["src"]' in pyproject, (
        "pyproject.toml no longer sets pythonpath = [\"src\"] under "
        "[tool.pytest.ini_options]; pytest may import an installed copy instead"
    )
