"""Guard against the working tree being shadowed by an installed wheel.

This project was previously installed non-editably from ``dist/*.whl``. As a
result ``pytest`` imported ``site-packages/lineagelens`` rather than ``src/``,
the two trees drifted by ~30 lines, and a green test run said nothing at all
about the code under development. Keep this test first in the suite.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

REMEDIATION = (
    "conda activate lineagelens && "
    "pip uninstall -y lineagelens && "
    'pip install -e ".[dev,web,mcp]"'
)


def test_lineagelens_imports_from_source_tree() -> None:
    import lineagelens

    resolved = Path(lineagelens.__file__).resolve()
    assert resolved.is_relative_to(SRC_ROOT), (
        f"imported lineagelens from {resolved}, expected a path under {SRC_ROOT}. "
        f"A stale installed copy is shadowing the working tree. Fix with:\n  {REMEDIATION}"
    )


def test_no_stale_wheel_in_dist_is_installed() -> None:
    """A non-editable install records an ``archive_info`` direct_url; editable does not."""
    import json
    from importlib.metadata import Distribution, PackageNotFoundError

    try:
        dist = Distribution.from_name("lineagelens")
    except PackageNotFoundError:
        return  # not installed at all; pythonpath is doing the work

    raw = dist.read_text("direct_url.json")
    if not raw:
        return
    info = json.loads(raw)
    assert "archive_info" not in info, (
        f"lineagelens is installed non-editably from {info.get('url')}. "
        f"Fix with:\n  {REMEDIATION}"
    )
