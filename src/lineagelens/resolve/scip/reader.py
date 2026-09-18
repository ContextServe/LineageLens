"""A minimal SCIP protobuf reader (#47 step A).

Reads the subset of `scip.proto` LineageLens needs, directly off the protobuf
wire format. No `protobuf` runtime and no generated bindings.

**Why not generated bindings.** Vendoring `scip_pb2.py` requires the protobuf
runtime at import time and pins a gencode version that breaks against other
runtime versions. Tier A extraction is deliberately non-optional and depends on
no external toolchain (#51 §7.1); a compiler-grade *upgrade* should not be able
to make the base case fail to import. The wire format for the fields below is
also stable in a way a gencode contract is not: field numbers are append-only
by protobuf's own compatibility rules.

**Field numbers are transcribed from the upstream `scip.proto`**, not inferred.
An off-by-one or a wrong tag here would silently resolve nothing rather than
fail loudly, which is the worst available outcome, so:

* the parser *raises* on a truncated or malformed buffer rather than returning
  partial data;
* :func:`read_index` raises when an index contains no documents, because an
  empty parse and an empty index are indistinguishable otherwise;
* a round-trip test writes a synthetic index with these same numbers and reads
  it back, and a second test asserts the numbers against the values recorded in
  this docstring.

    Index              metadata=1  documents=2  external_symbols=3
    Metadata           version=1  tool_info=2  project_root=3
    Document           relative_path=1  occurrences=2  symbols=3
                       language=4  text=5
    Occurrence         range=1 (deprecated)  symbol=2  symbol_roles=3
                       single_line_range=8  multi_line_range=9
    SingleLineRange    line=1  start_character=2  end_character=3
    MultiLineRange     start_line=1  start_character=2
                       end_line=3  end_character=4
    SymbolRole         Definition=0x1
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: `SymbolRole.Definition`. An occurrence carrying this bit *is* the definition
#: site; everything else is a reference to one.
DEFINITION_ROLE = 0x1

# Wire types.
_VARINT, _FIXED64, _LENGTH, _FIXED32 = 0, 1, 2, 5


class ScipParseError(ValueError):
    """The buffer is not a readable SCIP index.

    Raised rather than tolerated. A partially-parsed index produces confident
    edges that do not correspond to the code, which is the class of failure
    #51 exists to eliminate.
    """


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise ScipParseError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ScipParseError("varint exceeds 64 bits")


def _fields(buf: bytes):
    """Yield ``(field_number, wire_type, value)`` for one message body.

    ``value`` is an int for varints and a ``memoryview`` slice for
    length-delimited fields, so nesting does not copy.
    """
    pos = 0
    end = len(buf)
    while pos < end:
        tag, pos = _varint(buf, pos)
        number, wire = tag >> 3, tag & 0x07
        if wire == _VARINT:
            value, pos = _varint(buf, pos)
            yield number, wire, value
        elif wire == _LENGTH:
            length, pos = _varint(buf, pos)
            if pos + length > end:
                raise ScipParseError(
                    f"length-delimited field {number} overruns the buffer"
                )
            yield number, wire, buf[pos:pos + length]
            pos += length
        elif wire == _FIXED64:
            pos += 8
            yield number, wire, None
        elif wire == _FIXED32:
            pos += 4
            yield number, wire, None
        else:
            # Groups (3, 4) are removed from proto3 and never appear in SCIP.
            raise ScipParseError(f"unsupported wire type {wire}")


def _packed_varints(buf: bytes) -> list[int]:
    out: list[int] = []
    pos = 0
    while pos < len(buf):
        value, pos = _varint(buf, pos)
        out.append(value)
    return out


@dataclass(frozen=True, slots=True)
class ScipOccurrence:
    """One symbol occurrence, with positions already converted.

    ``line`` and ``column`` are **1-indexed lines, 0-indexed columns**, matching
    :class:`~lineagelens.core.span.Span`. SCIP is 0-indexed on both, so the
    conversion happens here -- once, at the boundary -- rather than at every
    lookup site.
    """

    symbol: str
    line: int
    column: int
    end_line: int
    end_column: int
    roles: int = 0

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & DEFINITION_ROLE)


@dataclass(slots=True)
class ScipDocument:
    relative_path: str
    language: str = ""
    text: str = ""
    occurrences: list[ScipOccurrence] = field(default_factory=list)


@dataclass(slots=True)
class ScipIndex:
    project_root: str = ""
    tool: str = ""
    documents: list[ScipDocument] = field(default_factory=list)

    def occurrence_count(self) -> int:
        return sum(len(d.occurrences) for d in self.documents)


def _read_range(buf: bytes, *, single_line: bool) -> tuple[int, int, int, int]:
    """A SingleLineRange or MultiLineRange, still 0-indexed."""
    values: dict[int, int] = {}
    for number, wire, value in _fields(buf):
        if wire == _VARINT:
            values[number] = value
    if single_line:
        line = values.get(1, 0)
        return line, values.get(2, 0), line, values.get(3, 0)
    return (
        values.get(1, 0), values.get(2, 0),
        values.get(3, values.get(1, 0)), values.get(4, 0),
    )


def _read_occurrence(buf: bytes) -> ScipOccurrence | None:
    symbol = ""
    roles = 0
    legacy: list[int] = []
    typed: tuple[int, int, int, int] | None = None

    for number, wire, value in _fields(buf):
        if number == 1 and wire == _LENGTH:
            # Packed `repeated int32 range`, deprecated but still what most
            # indexers emit today.
            legacy = _packed_varints(bytes(value))
        elif number == 1 and wire == _VARINT:
            legacy.append(value)
        elif number == 2 and wire == _LENGTH:
            symbol = bytes(value).decode("utf-8", "replace")
        elif number == 3 and wire == _VARINT:
            roles = value
        elif number == 8 and wire == _LENGTH:
            typed = _read_range(bytes(value), single_line=True)
        elif number == 9 and wire == _LENGTH:
            typed = _read_range(bytes(value), single_line=False)

    if not symbol:
        return None

    if typed is None:
        # `range` is three or four elements: [line, startChar, endChar] with
        # the end line inferred, or [startLine, startChar, endLine, endChar].
        if len(legacy) == 3:
            typed = (legacy[0], legacy[1], legacy[0], legacy[2])
        elif len(legacy) == 4:
            typed = (legacy[0], legacy[1], legacy[2], legacy[3])
        else:
            return None

    start_line, start_col, end_line, end_col = typed
    # The single conversion point. SCIP lines are 0-indexed; Span lines are
    # 1-indexed. Columns are 0-indexed in both.
    return ScipOccurrence(
        symbol=symbol,
        line=start_line + 1,
        column=start_col,
        end_line=end_line + 1,
        end_column=end_col,
        roles=roles,
    )


def _read_document(buf: bytes) -> ScipDocument:
    document = ScipDocument(relative_path="")
    for number, wire, value in _fields(buf):
        if wire != _LENGTH:
            continue
        if number == 1:
            document.relative_path = bytes(value).decode("utf-8", "replace")
        elif number == 2:
            occurrence = _read_occurrence(bytes(value))
            if occurrence is not None:
                document.occurrences.append(occurrence)
        elif number == 4:
            document.language = bytes(value).decode("utf-8", "replace")
        elif number == 5:
            document.text = bytes(value).decode("utf-8", "replace")
    return document


def _read_metadata(buf: bytes) -> tuple[str, str]:
    project_root = tool = ""
    for number, wire, value in _fields(buf):
        if wire != _LENGTH:
            continue
        if number == 3:
            project_root = bytes(value).decode("utf-8", "replace")
        elif number == 2:
            # ToolInfo.name is field 1.
            for inner_number, inner_wire, inner in _fields(bytes(value)):
                if inner_number == 1 and inner_wire == _LENGTH:
                    tool = bytes(inner).decode("utf-8", "replace")
    return project_root, tool


def read_index(path: Path | str) -> ScipIndex:
    """Parse a SCIP index file.

    Raises :class:`ScipParseError` for a malformed buffer *or* for a
    well-formed one containing no documents -- because a successful parse of
    nothing and a failed parse look the same to a caller, and "SCIP resolved
    nothing" must not be reported as "SCIP found nothing to resolve".
    """
    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ScipParseError(f"cannot read {file_path}: {exc}") from exc

    if not raw:
        raise ScipParseError(f"{file_path} is empty")

    index = ScipIndex()
    for number, wire, value in _fields(raw):
        if wire != _LENGTH:
            continue
        if number == 1:
            index.project_root, index.tool = _read_metadata(bytes(value))
        elif number == 2:
            index.documents.append(_read_document(bytes(value)))

    if not index.documents:
        raise ScipParseError(
            f"{file_path} parsed with no documents. Either it is not a SCIP "
            f"index, or it was produced by an indexer that found no files."
        )
    return index
