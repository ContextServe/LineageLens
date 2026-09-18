"""Encode a SCIP index at the wire level (#47).

Writes protobuf by hand rather than using generated bindings, for the same
reason the reader parses by hand: no runtime dependency, and the field numbers
are transcribed from the upstream `scip.proto` in both places.

That gives a genuine round trip -- these numbers and the reader's must agree or
nothing parses -- but it is **not** a test against real indexer output. Nothing
here proves the reader handles a file emitted by `scip-java`; only that it
handles the format as documented. See the note in `resolve/scip/reader.py`.

    Index              metadata=1  documents=2
    Metadata           tool_info=2 {name=1}  project_root=3
    Document           relative_path=1  occurrences=2  language=4  text=5
    Occurrence         range=1 (packed)  symbol=2  symbol_roles=3
    SymbolRole         Definition=0x1
"""

def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def tag(num, wire): return varint((num << 3) | wire)
def lenf(num, payload): return tag(num, 2) + varint(len(payload)) + payload
def varf(num, val): return tag(num, 0) + varint(val)

def occurrence(symbol, line, start_col, end_col, roles=0):
    """line/start_col are 0-indexed, as SCIP requires."""
    body = lenf(1, varint(line) + varint(start_col) + varint(end_col))
    body += lenf(2, symbol.encode())
    if roles:
        body += varf(3, roles)
    return body

def document(relative_path, occurrences, language="java", text=None):
    body = lenf(1, relative_path.encode())
    for occ in occurrences:
        body += lenf(2, occ)
    body += lenf(4, language.encode())
    if text is not None:
        body += lenf(5, text.encode())
    return body

def index(documents, tool="scip-java", project_root="file:///repo"):
    meta = lenf(2, lenf(1, tool.encode())) + lenf(3, project_root.encode())
    out = lenf(1, meta)
    for doc in documents:
        out += lenf(2, doc)
    return out
