"""Source locations, carried by both nodes and edges.

Schema 3 stored ``line``/``end_line`` on symbols only. That made two of the
required capabilities unreachable: you cannot answer "what does changing
``file.py:412`` affect" without knowing which *operations* live on line 412, and
you cannot describe data flow through a subexpression you cannot point at.

So every node and every edge carries a span, and the span carries byte offsets
as well as line/column. Byte offsets are the authority -- they are exact,
encoding-independent, and cheap for tree-sitter to produce; line/column are
derived and stored for human display and for the line-intersection index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True, order=True)
class Span:
    """A half-open byte range ``[start_byte, end_byte)`` plus its line/col view.

    Field order matters: ``order=True`` sorts by start then end, so sorting a
    list of spans yields outermost-first at any shared start offset, and
    ``max()`` over containing spans yields the innermost. Both are relied on by
    :meth:`innermost_of`.

    Lines are 1-based (what editors and stack traces show); columns are 0-based
    (what tree-sitter reports). Mixing the two conventions is a common source of
    off-by-one bugs, so it is stated once here and never re-derived.
    """

    start_byte: int
    end_byte: int
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def __post_init__(self) -> None:
        if self.end_byte < self.start_byte:
            raise ValueError(f"span end_byte {self.end_byte} precedes start_byte {self.start_byte}")
        if self.end_line < self.start_line:
            raise ValueError(f"span end_line {self.end_line} precedes start_line {self.start_line}")

    # ---- construction -----------------------------------------------------

    @classmethod
    def from_tree_sitter(cls, node: Any) -> Span:
        """Build a span from a tree-sitter node.

        tree-sitter gives ``start_point``/``end_point`` as 0-based ``(row, col)``
        tuples, so rows are shifted into the 1-based line convention here and
        nowhere else.
        """
        start_row, start_col = node.start_point
        end_row, end_col = node.end_point
        return cls(
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            start_line=start_row + 1,
            start_col=start_col,
            end_line=end_row + 1,
            end_col=end_col,
        )

    @classmethod
    def whole_file(cls, content: bytes) -> Span:
        """The span covering an entire file, for ``file``-kind nodes."""
        line_count = content.count(b"\n") + 1
        last_newline = content.rfind(b"\n")
        end_col = len(content) - last_newline - 1 if last_newline >= 0 else len(content)
        return cls(
            start_byte=0,
            end_byte=len(content),
            start_line=1,
            start_col=0,
            end_line=line_count,
            end_col=end_col,
        )

    @classmethod
    def at_line(cls, line: int) -> Span:
        """A degenerate span used to query by line when byte offsets are unknown.

        Only for lookups -- never stored. Byte offsets are zero because they are
        meaningless here, which is also why this must not reach the store.
        """
        return cls(start_byte=0, end_byte=0, start_line=line, start_col=0, end_line=line, end_col=0)

    # ---- predicates -------------------------------------------------------

    @property
    def is_multiline(self) -> bool:
        return self.end_line > self.start_line

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    @property
    def byte_length(self) -> int:
        return self.end_byte - self.start_byte

    def contains_line(self, line: int) -> bool:
        """Does this span cover ``line``? Inclusive at both ends.

        Used to find the node being edited: the innermost node whose span
        contains the changed line.
        """
        return self.start_line <= line <= self.end_line

    def intersects_line(self, line: int) -> bool:
        """Alias of :meth:`contains_line`, named for the edge-anchoring case.

        An edge's span is usually a single expression, so "does this operation
        appear on the changed line" reads better as an intersection.
        """
        return self.contains_line(line)

    def contains_byte(self, offset: int) -> bool:
        return self.start_byte <= offset < self.end_byte

    def contains(self, other: Span) -> bool:
        """Does this span fully enclose ``other``? A span contains itself."""
        return self.start_byte <= other.start_byte and other.end_byte <= self.end_byte

    def overlaps(self, other: Span) -> bool:
        return self.start_byte < other.end_byte and other.start_byte < self.end_byte

    # ---- selection --------------------------------------------------------

    @staticmethod
    def innermost_of(spans: list[Span]) -> Span | None:
        """The tightest span in ``spans``, i.e. the most specific match.

        When several nodes contain a line -- module, class, method -- the
        interesting one is the method. Ties on byte length are broken by later
        start offset so the result is deterministic (§11) rather than dependent
        on input order.
        """
        if not spans:
            return None
        return min(spans, key=lambda s: (s.byte_length, -s.start_byte))

    # ---- serialisation ----------------------------------------------------

    def as_row(self) -> tuple[int, int, int, int, int, int]:
        """Column order matching the ``nodes``/``edges`` span columns."""
        return (
            self.start_byte, self.end_byte,
            self.start_line, self.start_col,
            self.end_line, self.end_col,
        )

    @classmethod
    def from_row(cls, row: Any) -> Span:
        """Rebuild from a ``sqlite3.Row`` (or any mapping with the span keys)."""
        return cls(
            start_byte=row["start_byte"],
            end_byte=row["end_byte"],
            start_line=row["start_line"],
            start_col=row["start_col"],
            end_line=row["end_line"],
            end_col=row["end_col"],
        )

    def __str__(self) -> str:
        if self.is_multiline:
            return f"{self.start_line}:{self.start_col}-{self.end_line}:{self.end_col}"
        return f"{self.start_line}:{self.start_col}-{self.end_col}"
