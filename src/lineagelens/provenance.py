"""Git provenance for an index (#66).

`graph_meta.commit_sha` existed as a column and was never written, so a graph
could not be tied to the commit it describes. Three consumers need it: SCIP
staleness detection compares the index against the working tree (#47),
`index --upload` needs commit and branch identity for per-branch attribution
(#58, #59), and `impact_of_diff` reports the impact of a diff with no record of
what it was diffed against.

Two rules govern everything here.

**Git is never a dependency.** A non-git directory, a shallow checkout, a
missing `git` binary, a detached HEAD, or a repository with no commits must all
leave the fields ``None`` and let indexing proceed. Someone indexing an
unpacked tarball is a normal user.

**`dirty` is what makes `commit_sha` honest.** A graph built from a modified
working tree is not the graph of that commit, and a consumer told the commit but
not the modification will trust it. So the two are resolved together and a
failure to determine one does not silently report the other as clean.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Git can hang on a network remote or a lock held by another process. Indexing
#: must not inherit that, so every call is bounded and a timeout is treated
#: exactly like "not a repository".
_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Provenance:
    """What commit an index was built from, when that is knowable."""

    commit_sha: str | None = None
    branch: str | None = None
    #: ``None`` means "could not determine", which is not the same as ``False``.
    #: Reporting an unknown tree as clean is the failure this field exists to
    #: prevent, so it stays tri-state.
    dirty: bool | None = None

    @property
    def known(self) -> bool:
        return self.commit_sha is not None


def _git(root: Path, *args: str) -> str | None:
    """Run one git command in ``root``, or return None for any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # FileNotFoundError (no git), TimeoutExpired, and anything else the
        # subprocess machinery raises all mean the same thing to us.
        logger.debug("git %s failed in %s: %s", " ".join(args), root, exc)
        return None
    if result.returncode != 0:
        logger.debug("git %s exited %d: %s", " ".join(args),
                     result.returncode, result.stderr.strip()[:200])
        return None
    return result.stdout.strip()


def resolve(root: Path | str) -> Provenance:
    """Commit, branch and dirty state for ``root``, best effort.

    Returns an empty :class:`Provenance` rather than raising, for any reason at
    all. The caller is an indexer whose job is to produce a graph, and the
    absence of git metadata is not a reason to fail at that.
    """
    path = Path(root)

    commit = _git(path, "rev-parse", "HEAD")
    if not commit:
        # Not a checkout, no commits yet, or no git. Everything else would be
        # meaningless without a commit to attach it to, so stop here.
        return Provenance()

    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        # Detached. The commit is still the truth; there is no branch, and
        # reporting the literal string "HEAD" as a branch name would be worse
        # than reporting none.
        branch = None

    # `--porcelain` is the stable, parseable form. Empty output means clean.
    # A None here stays None rather than becoming False: an unknown tree
    # reported as clean is precisely the misplaced trust this avoids.
    status = _git(path, "status", "--porcelain", "--untracked-files=no")
    dirty = None if status is None else bool(status)

    return Provenance(commit_sha=commit, branch=branch or None, dirty=dirty)


__all__ = ["Provenance", "resolve"]
