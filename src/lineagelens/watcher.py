"""Watch mode: keep the index current as files change.

Ported to schema 4 rather than deleted. It is small and it is the difference
between an index that is trustworthy during a working session and one that
silently goes stale.

Reindexing is a whole-project rebuild rather than a per-file patch. That looks
wasteful and is not: resolution is global, because a call can target any file,
so patching one file's nodes without re-resolving its dependents would leave
edges pointing at symbols that no longer exist. The measured cost is 21s for
Apache Dubbo's 4,046 files, and a debounce means a burst of saves triggers one
rebuild.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from .core import DataflowMode
from .indexer import Indexer
from .services import IGNORED_DIRS

logger = logging.getLogger(__name__)

#: Seconds of quiet before rebuilding. A formatter-on-save or a branch switch
#: touches many files at once; without this each one would queue a rebuild.
DEBOUNCE_SECONDS = 1.5


class IndexWatcher:
    """Rebuilds the index after a burst of file changes settles."""

    def __init__(
        self,
        project: Path,
        *,
        dataflow: DataflowMode = DataflowMode.LAZY,
        allow_tier_a_only: frozenset[str] = frozenset(),
        debounce: float = DEBOUNCE_SECONDS,
    ) -> None:
        self.project = Path(project).resolve()
        self.dataflow = dataflow
        self.allow_tier_a_only = allow_tier_a_only
        self.debounce = debounce
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    # ---- public API -------------------------------------------------------

    def run(self) -> int:
        """Watch until interrupted. Returns a process exit code."""
        try:
            from watchdog.observers import Observer
        except ImportError:
            logger.error(
                "watch mode needs watchdog: pip install 'lineagelens[watch]'"
            )
            return 1

        handler = _ChangeHandler(self)
        observer = Observer()
        observer.schedule(handler, str(self.project), recursive=True)
        observer.start()

        print(f"watching {self.project} (ctrl-c to stop)")
        self.reindex(reason="initial")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nstopping")
        finally:
            observer.stop()
            observer.join()
        return 0

    def note_change(self, path: str) -> None:
        """Record a change and (re)start the debounce timer."""
        with self._lock:
            self._pending.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def reindex(self, *, reason: str) -> None:
        store, report = Indexer(
            self.project,
            dataflow=self.dataflow,
            allow_tier_a_only=self.allow_tier_a_only,
        ).run()
        try:
            print(
                f"  {reason}: {report.nodes:,} nodes, {report.edges:,} edges, "
                f"{report.duration_seconds:.1f}s  [{report.build_digest[:12]}]"
            )
        finally:
            store.close()

    # ---- internals --------------------------------------------------------

    def _fire(self) -> None:
        with self._lock:
            changed = sorted(self._pending)
            self._pending.clear()
            self._timer = None
        if not changed:
            return
        head = ", ".join(Path(p).name for p in changed[:3])
        suffix = f" (+{len(changed) - 3} more)" if len(changed) > 3 else ""
        try:
            self.reindex(reason=f"{head}{suffix}")
        except Exception as exc:
            # A watcher that dies on one bad intermediate save is useless; the
            # next save retries, and the error is reported rather than hidden.
            logger.error("reindex failed: %s", exc)


def _relevant(path: str, project: Path) -> bool:
    """Is this a path worth reindexing for?

    Filters the index's own output first: writing ``.lineagelens/graph.sqlite``
    would otherwise trigger the next rebuild, which triggers the next.
    """
    candidate = Path(path)
    try:
        relative = candidate.resolve().relative_to(project)
    except ValueError:
        return False
    if any(part in IGNORED_DIRS or part.startswith(".") for part in relative.parts):
        return False

    from .extract import detect_dialect

    return detect_dialect(candidate) is not None


class _ChangeHandler:
    """watchdog handler. Duck-typed so importing watchdog stays optional."""

    def __init__(self, watcher: IndexWatcher) -> None:
        self.watcher = watcher

    def dispatch(self, event: object) -> None:
        if getattr(event, "is_directory", False):
            return
        for attribute in ("src_path", "dest_path"):
            path = getattr(event, attribute, None)
            if path and _relevant(str(path), self.watcher.project):
                self.watcher.note_change(str(path))
                return
