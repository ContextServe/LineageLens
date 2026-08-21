"""Analysis execution report with failure/warning tracking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class FileFailure:
    """Record of a file that could not be analyzed."""

    file: str
    stage: Literal["read", "parse", "definitions", "relationships"]
    error_type: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"{self.file}{loc}: [{self.stage}] {self.error_type}: {self.message}"


@dataclass
class SymbolWarning:
    """Warning about a symbol during analysis (e.g., unresolved reference)."""

    symbol_id: str | None
    file: str
    line: int
    message: str
    stage: str

    def __str__(self) -> str:
        sym_str = f" ({self.symbol_id})" if self.symbol_id else ""
        return f"{self.file}:{self.line}{sym_str}: {self.stage}: {self.message}"


@dataclass
class AnalysisReport:
    """Summary of a code analysis run."""

    project_root: str
    started_at: str
    finished_at: str
    files_scanned: int
    files_skipped: int
    symbols_found: int
    relations_found: int
    containers_found: int
    failures: list[FileFailure] = field(default_factory=list)
    warnings: list[SymbolWarning] = field(default_factory=list)

    def is_clean(self) -> bool:
        """True if analysis completed without failures or warnings."""
        return len(self.failures) == 0 and len(self.warnings) == 0

    def has_failures(self) -> bool:
        """True if any files failed to analyze."""
        return len(self.failures) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)

    def summary_lines(self) -> list[str]:
        """Human-readable summary lines for CLI output."""
        lines = [
            f"Analysis Report: {self.project_root}",
            f"  Time: {self.started_at} → {self.finished_at}",
            f"  Files scanned: {self.files_scanned} (skipped: {self.files_skipped})",
            f"  Symbols found: {self.symbols_found}",
            f"  Relations found: {self.relations_found}",
            f"  Containers found: {self.containers_found}",
        ]

        if self.failures:
            lines.append(f"\n❌ Failures ({len(self.failures)}):")
            for failure in self.failures[:10]:  # Show first 10
                lines.append(f"  {failure}")
            if len(self.failures) > 10:
                lines.append(f"  ... and {len(self.failures) - 10} more")

        if self.warnings:
            lines.append(f"\n⚠️  Warnings ({len(self.warnings)}):")
            for warning in self.warnings[:5]:  # Show first 5
                lines.append(f"  {warning}")
            if len(self.warnings) > 5:
                lines.append(f"  ... and {len(self.warnings) - 5} more")

        if self.is_clean():
            lines.append("\n✅ Analysis completed successfully with no issues.")

        return lines
