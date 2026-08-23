"""Jedi-backed call resolution for LineageLens.

Uses Jedi (the same static type-inference engine as PyCharm/VS Code) to resolve
variable-through-instance method calls: e.g., conn.apply_migrations() where conn
is assigned a DatabaseConnection instance.

This module is defensive: all Jedi calls are wrapped in try/except. Failures are
logged to the report and execution continues (never crashes per LineageLens's
documented guarantee).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JediTarget:
    """A target symbol resolved by Jedi."""

    module_path: Path
    line: int
    column: int
    type: str  # jedi's "function"/"class"/"instance"/"module" etc.
    full_name: str | None


class JediResolver:
    """Lazily initialized Jedi project and per-file script cache.

    Wraps Jedi's inference with defensive error handling. All Jedi calls
    that raise exceptions are logged but do not abort analysis.
    """

    def __init__(self, root: Path) -> None:
        """Initialize Jedi resolver for a project root.

        Args:
            root: Project root directory (passed to jedi.Project)
        """
        self.root = root
        self._project: Any = None  # Lazy: jedi.Project(path=root)
        self._scripts: dict[Path, Any] = {}  # Lazy: {file_path: jedi.Script(...)}

    def _get_project(self) -> Any:
        """Lazily create and return the Jedi project."""
        if self._project is None:
            try:
                import jedi

                self._project = jedi.Project(path=str(self.root))
            except Exception as e:
                logger.warning(f"Failed to initialize Jedi project for {self.root}: {e}")
                self._project = False  # Sentinel: Jedi unavailable
        return self._project if self._project is not False else None

    def _get_script(self, file_path: Path) -> Any:
        """Lazily create and cache a Jedi Script for a file."""
        if file_path in self._scripts:
            return self._scripts[file_path]
        try:
            import jedi

            project = self._get_project()
            if not project:
                return None
            script = jedi.Script(path=str(file_path), project=project)
            self._scripts[file_path] = script
            return script
        except Exception as e:
            logger.warning(f"Failed to create Jedi script for {file_path}: {e}")
            self._scripts[file_path] = None
            return None

    def resolve_call(self, file_path: Path, line: int, column: int) -> list[JediTarget]:
        """Resolve a call site to its target symbol(s) using Jedi.

        Args:
            file_path: Absolute path to the Python file
            line: Line number (1-indexed, matching ast.lineno)
            column: Column number (0-indexed, matching ast.col_offset)

        Returns:
            List of JediTarget objects (empty if unresolvable or Jedi fails)
        """
        try:
            script = self._get_script(file_path)
            if not script:
                return []

            # Jedi.goto() returns a list of Definition objects
            definitions = script.goto(line=line, column=column, follow_imports=True)

            results: list[JediTarget] = []
            for defn in definitions:
                try:
                    results.append(
                        JediTarget(
                            module_path=Path(defn.module_path) if defn.module_path else file_path,
                            line=defn.line or 0,
                            column=defn.column or 0,
                            type=defn.type or "unknown",
                            full_name=defn.full_name,
                        )
                    )
                except Exception as e:
                    logger.debug(f"Failed to extract JediTarget from definition: {e}")
                    continue

            return results
        except Exception as e:
            logger.warning(f"Jedi resolution failed for {file_path}:{line}:{column}: {e}")
            return []
