"""Extract usage sites: where symbols are imported, called, etc.

This module identifies how external packages and internal symbols are used
within the codebase, enabling queries like "where is anthropic.Anthropic used?"
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class UsageSite:
    """A place where a symbol is used in the codebase."""

    symbol_name: str  # "anthropic.Anthropic" or "ChatAnthropic"
    usage_type: str  # "import", "call", "instantiate", "attribute_access"
    file_path: str
    line_number: int
    context_line: str
    context_before: str | None = None
    context_after: str | None = None


class UsageExtractor(ast.NodeVisitor):
    """Extract usage sites from Python AST."""

    def __init__(self, file_path: str, source: str):
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.usages: list[UsageSite] = []
        self.current_scope = ""

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Track: from anthropic import Anthropic."""
        if node.module:
            for alias in node.names:
                # from X import Y
                full_name = f"{node.module}.{alias.name}"
                context_before, context_line, context_after = self._get_context(node.lineno)
                self.usages.append(
                    UsageSite(
                        symbol_name=full_name,
                        usage_type="import",
                        file_path=self.file_path,
                        line_number=node.lineno,
                        context_line=context_line,
                        context_before=context_before,
                        context_after=context_after,
                    )
                )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """Track: import anthropic."""
        for alias in node.names:
            context_before, context_line, context_after = self._get_context(node.lineno)
            self.usages.append(
                UsageSite(
                    symbol_name=alias.name,
                    usage_type="import",
                    file_path=self.file_path,
                    line_number=node.lineno,
                    context_line=context_line,
                    context_before=context_before,
                    context_after=context_after,
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Track method calls and instantiations."""
        # Extract the function being called
        func_name = self._extract_name(node.func)
        if func_name:
            context_before, context_line, context_after = self._get_context(node.lineno)
            self.usages.append(
                UsageSite(
                    symbol_name=func_name,
                    usage_type="call",
                    file_path=self.file_path,
                    line_number=node.lineno,
                    context_line=context_line,
                    context_before=context_before,
                    context_after=context_after,
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Track attribute access: obj.attr."""
        # This is called for every attribute access, including in calls
        # We mainly care about top-level ones
        self.generic_visit(node)

    def _extract_name(self, node: ast.expr) -> str | None:
        """Extract a dotted name from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value = self._extract_name(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        return None

    def _get_line(self, line_number: int) -> str:
        """Get source line by number."""
        if 1 <= line_number <= len(self.lines):
            return self.lines[line_number - 1].strip()
        return ""

    def _get_context(
        self, line_number: int, context_lines: int = 1
    ) -> tuple[str | None, str, str | None]:
        """Get context: (line_before, line, line_after)."""
        before = None
        after = None
        line = self._get_line(line_number)

        if line_number - 1 >= 1:
            before = self.lines[line_number - 2].strip()
        if line_number < len(self.lines):
            after = self.lines[line_number].strip()

        return before, line, after


def extract_usages(file_path: str, source: str) -> list[UsageSite]:
    """Extract all usage sites from a Python file.

    Args:
        file_path: Path to the Python file (for reference)
        source: Source code content

    Returns:
        List of UsageSite objects
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    extractor = UsageExtractor(file_path, source)
    extractor.visit(tree)
    return extractor.usages
