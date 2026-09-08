"""Git hook that keeps the index current on checkout and commit.

Ported to schema 4. The hook body is deliberately trivial -- it shells out to
``lineagelens index`` -- so a schema change never requires reinstalling hooks.
"""

from __future__ import annotations

import logging
import stat
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: Hooks worth installing. post-checkout catches branch switches, which change
#: more files than any edit and are the commonest way an index goes stale.
SUPPORTED = ("post-commit", "post-checkout", "post-merge")

_TEMPLATE = """#!/bin/sh
# Installed by lineagelens. Keeps .lineagelens/ current.
# Non-blocking: a failed index must never block a commit or a checkout.
command -v lineagelens >/dev/null 2>&1 || exit 0
lineagelens index "$(git rev-parse --show-toplevel)" >/dev/null 2>&1 &
exit 0
"""

_MARKER = "Installed by lineagelens"


def hooks_dir(project: Path) -> Path:
    """The repository's hook directory, honouring ``core.hooksPath``.

    Asked of git rather than assumed to be ``.git/hooks``: worktrees and a
    configured ``core.hooksPath`` both move it, and writing to the wrong place
    silently installs nothing.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=project, capture_output=True, text=True, check=True,
        ).stdout.strip()
        configured = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=project, capture_output=True, text=True, check=False,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(f"{project} is not a git repository: {exc}") from exc

    if configured:
        path = Path(configured)
        return path if path.is_absolute() else project / path
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = project / common_path
    return common_path / "hooks"


def install(project: Path, *, kind: str = "post-commit", force: bool = False) -> Path:
    """Install one hook. Returns the path written."""
    if kind not in SUPPORTED:
        raise ValueError(f"unsupported hook {kind!r}; choose from {SUPPORTED}")

    target = hooks_dir(project) / kind
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and _MARKER not in target.read_text("utf-8", errors="replace"):
        if not force:
            raise RuntimeError(
                f"{target} exists and was not installed by lineagelens; "
                f"pass force=True to overwrite"
            )
        logger.warning("overwriting an existing %s hook", kind)

    target.write_text(_TEMPLATE)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def uninstall(project: Path, *, kind: str = "post-commit") -> bool:
    """Remove a hook, but only one this tool installed."""
    target = hooks_dir(project) / kind
    if not target.exists():
        return False
    if _MARKER not in target.read_text("utf-8", errors="replace"):
        logger.warning("%s was not installed by lineagelens; leaving it alone", kind)
        return False
    target.unlink()
    return True


def status(project: Path) -> dict[str, bool]:
    """Which supported hooks are currently installed by this tool."""
    directory = hooks_dir(project)
    out: dict[str, bool] = {}
    for kind in SUPPORTED:
        path = directory / kind
        out[kind] = (
            path.exists()
            and _MARKER in path.read_text("utf-8", errors="replace")
        )
    return out
