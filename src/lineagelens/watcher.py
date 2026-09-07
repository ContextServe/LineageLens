"""Standalone background watcher daemon for LineageLens (lineagelens watch)."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import ProjectConfig
from .db import DB_NAME, SQLiteIndexDB
from .incremental import IncrementalAnalyzer
from .treesitter_analyzer import EXTENSION_LANG_MAP

logger = logging.getLogger(__name__)

PID_FILE_NAME = "watcher.pid"
DEBOUNCE_INTERVAL_SEC = 0.3  # 300ms debounce window


class DebouncedEventHandler(FileSystemEventHandler):
    """Debounces rapid file modification events before triggering incremental sync."""

    def __init__(
        self,
        project_root: Path,
        incremental_analyzer: IncrementalAnalyzer,
        db: SQLiteIndexDB,
        debounce_sec: float = DEBOUNCE_INTERVAL_SEC,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.incremental_analyzer = incremental_analyzer
        self.db = db
        self.debounce_sec = debounce_sec
        self.pending_files: set[str] = set()
        self.lock = threading.Lock()
        self.timer: threading.Timer | None = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        src_path = Path(event.src_path)
        if self._is_ignored(src_path):
            return

        ext = src_path.suffix.lower()
        if ext not in EXTENSION_LANG_MAP and ext != ".py":
            return

        try:
            rel_path = str(src_path.relative_to(self.project_root))
        except ValueError:
            return

        with self.lock:
            self.pending_files.add(rel_path)
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(
                self.debounce_sec, self._process_pending_files
            )
            self.timer.start()

    def _is_ignored(self, path: Path) -> bool:
        parts = path.parts
        return any(
            ignored in parts
            for ignored in (
                ".git",
                ".lineagelens",
                "node_modules",
                ".venv",
                "__pycache__",
                "dist",
                "coverage",
            )
        )

    def _process_pending_files(self) -> None:
        with self.lock:
            files_to_sync = list(self.pending_files)
            self.pending_files.clear()
            self.timer = None

        for rel_file in files_to_sync:
            try:
                self.incremental_analyzer.sync_file(
                    self.project_root, rel_file, self.db
                )
            except Exception as exc:
                logger.error(f"Error syncing {rel_file}: {exc}")


class LineageLensWatcher:
    """Manages file monitoring observer and daemon lifecycle."""

    def __init__(self, project_root: Path | str, config: ProjectConfig | None = None) -> None:
        self.project_root = Path(project_root).resolve()
        config_path = self.project_root / "lineagelens.yaml"
        if config is not None:
            self.config = config
        elif config_path.exists():
            self.config = ProjectConfig.load(config_path)
        else:
            self.config = ProjectConfig()
        self.lineagelens_dir = self.project_root / ".lineagelens"
        self.lineagelens_dir.mkdir(parents=True, exist_ok=True)
        
        self.pid_file = self.lineagelens_dir / PID_FILE_NAME
        self.db = SQLiteIndexDB(self.lineagelens_dir / DB_NAME)
        self.incremental_analyzer = IncrementalAnalyzer(self.config)
        self.observer = Observer()

    def start(self, daemon: bool = False) -> None:
        """Start file watcher. If daemon is True, detach as background process."""
        status = self.get_status()
        if status["running"]:
            logger.info(f"Watcher already running (PID {status['pid']})")
            return

        if daemon:
            import subprocess
            cmd = [sys.executable, "-m", "lineagelens.cli", "watch", str(self.project_root)]
            kwargs: dict[str, Any] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform != "win32":
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **kwargs)
            logger.info(f"Started LineageLens watcher daemon (PID {proc.pid})")
            return

        # Record current PID
        self.pid_file.write_text(str(os.getpid()))

        event_handler = DebouncedEventHandler(
            self.project_root, self.incremental_analyzer, self.db
        )

        source_roots = [
            self.project_root / root for root in self.config.source_roots
        ]
        if not any(r.exists() for r in source_roots):
            source_roots = [self.project_root]

        for root in source_roots:
            if root.exists():
                self.observer.schedule(event_handler, str(root), recursive=True)

        logger.info(f"LineageLens watcher active on {self.project_root}")
        self.observer.start()

        if not daemon:
            try:
                while self.observer.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                self.stop()

    def stop(self) -> bool:
        """Stop running watcher daemon."""
        status = self.get_status()
        if not status["running"]:
            if self.pid_file.exists():
                self.pid_file.unlink()
            logger.info("Watcher is not running.")
            return False

        pid = status["pid"]
        if pid == os.getpid():
            if self.observer.is_alive():
                self.observer.stop()
                self.observer.join()
            if self.pid_file.exists():
                self.pid_file.unlink()
            return True

        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"Stopped watcher daemon (PID {pid})")
        except ProcessLookupError:
            logger.warning(f"Process {pid} not found.")
        finally:
            if self.pid_file.exists():
                self.pid_file.unlink()
        return True

    def get_status(self) -> dict[str, Any]:
        """Check if watcher process is currently running."""
        if not self.pid_file.exists():
            return {"running": False, "pid": None}

        try:
            pid = int(self.pid_file.read_text().strip())
        except ValueError:
            return {"running": False, "pid": None}

        try:
            # Signal 0 checks if process exists without killing it
            os.kill(pid, 0)
            return {"running": True, "pid": pid}
        except (ProcessLookupError, OSError):
            return {"running": False, "pid": pid}
