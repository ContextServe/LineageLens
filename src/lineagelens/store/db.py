"""The only component that writes a graph.

Everything else -- extractors, oracles, resolvers, adapters -- hands records to
this module and never touches SQLite. That concentration is deliberate: schema 3
had two stores (``index.sqlite`` and ``graph.json``) with different fidelity and
a loader that silently preferred the lossy one, so 315 entry points and 3,326
argument lists existed on disk and were invisible to every consumer.

Three invariants this module enforces rather than documents:

* **Lossless.** Every field of every record type reaches a column. A round-trip
  test asserts equality field-by-field (§16.3).
* **Deterministic.** Rows are inserted in sorted order and ``build_digest``
  hashes the stored result, so two builds of one commit are byte-identical
  (§11). Insertion order leaking into the digest is the failure this prevents.
* **Accounted.** Coverage rows must balance, enforced by a CHECK constraint. A
  reference either becomes an edge or becomes an ``unresolved_refs`` row; it
  cannot vanish (§16.5).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from ..core import (
    SCHEMA_VERSION,
    Boundary,
    BoundaryKind,
    Coverage,
    Edge,
    EdgeKind,
    Evidence,
    EvidenceTier,
    FileRecord,
    Node,
    NodeFlags,
    NodeKind,
    ParseStatus,
    RefKind,
    RefStatus,
    Resolution,
    Service,
    SkipReason,
    Span,
    Tier,
    UnresolvedRef,
    Visibility,
    dumps,
    loads,
)
from ..core.types import ExtractorRun, ParseError
from .schema import CONNECTION_PRAGMAS, SCHEMA_DDL

logger = logging.getLogger(__name__)

DB_FILENAME = "graph.sqlite"


class SchemaMismatch(RuntimeError):
    """The index on disk was not written by this version of LineageLens.

    Raised rather than silently migrating or falling back. Schema 4 is a clean
    break (§17.2): there is no dual-read path, because the dual-read path is
    exactly how schema 3 came to serve a graph with zero entry points while a
    complete one sat beside it.
    """


class GraphNotFound(RuntimeError):
    """No index exists at the requested location."""


class GraphStore:
    """Read/write access to a schema-4 graph."""

    def __init__(self, db_path: Path | str, *, project_root: str | None = None) -> None:
        self.db_path = Path(db_path)
        self._project_root = project_root
        self._conn: sqlite3.Connection | None = None

    # ---- lifecycle --------------------------------------------------------

    @classmethod
    def create(
        cls,
        db_path: Path | str,
        *,
        project_root: str,
        commit_sha: str | None = None,
        branch: str | None = None,
        dirty: bool | None = None,
        overwrite: bool = True,
    ) -> GraphStore:
        """Initialise a fresh index, replacing any existing one.

        ``overwrite`` defaults to true because indexing is a full rebuild by
        default; incremental updates go through :meth:`purge_file` on an already
        open store rather than through here.
        """
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            for suffix in ("", "-wal", "-shm"):
                stale = Path(str(path) + suffix)
                if stale.exists():
                    stale.unlink()

        store = cls(path, project_root=project_root)
        conn = store._connect()
        conn.executescript(SCHEMA_DDL)
        conn.execute(
            """
            INSERT INTO graph_meta
                   (id, schema_version, project_root, commit_sha, branch, dirty)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                schema_version = excluded.schema_version,
                project_root   = excluded.project_root,
                commit_sha     = excluded.commit_sha,
                branch         = excluded.branch,
                dirty          = excluded.dirty
            """,
            (SCHEMA_VERSION, project_root, commit_sha, branch,
             None if dirty is None else int(dirty)),
        )
        conn.commit()
        return store

    @classmethod
    def open(cls, db_path: Path | str) -> GraphStore:
        """Open an existing index, refusing anything not written at the current schema."""
        path = Path(db_path)
        if not path.exists():
            raise GraphNotFound(
                f"No graph index at {path}\nRun: lineagelens index {path.parent.parent}"
            )
        store = cls(path)
        store._assert_schema()
        return store

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            for pragma in CONNECTION_PRAGMAS:
                conn.execute(pragma)
            self._conn = conn
        return self._conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._connect()

    def _assert_schema(self) -> None:
        try:
            row = self.conn.execute(
                "SELECT schema_version, project_root FROM graph_meta WHERE id = 1"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise SchemaMismatch(f"{self.db_path} is not a readable LineageLens index: {exc}") from exc

        if row is None:
            raise SchemaMismatch(f"{self.db_path} has no graph_meta row; reindex required")
        found = row["schema_version"]
        if found != SCHEMA_VERSION:
            raise SchemaMismatch(
                f"{self.db_path} was written at schema {found}, this build requires "
                f"{SCHEMA_VERSION}. There is no upgrade path between schema "
                f"versions by design -- reindex with: lineagelens index --force"
            )
        self._project_root = row["project_root"]

    @property
    def project_root(self) -> str:
        if self._project_root is None:
            self._assert_schema()
        return self._project_root or ""

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Group writes so a failed index leaves no half-written graph."""
        conn = self.conn
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    # ---- writes -----------------------------------------------------------
    #
    # Every write path sorts its input before inserting. That is what makes
    # build_digest reproducible: without it, dict/set iteration order leaks into
    # rowids and the digest differs between runs of the same commit.

    def write_services(self, services: Iterable[Service]) -> None:
        rows = sorted(
            ((s.id, s.name, s.root, s.kind, s.manifest_path) for s in services),
            key=lambda r: r[0],
        )
        self.conn.executemany(
            "INSERT OR REPLACE INTO services (id, name, root, kind, manifest_path) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    def write_file(self, record: FileRecord) -> int:
        """Insert or replace one file row, returning its assigned ``file_id``."""
        cur = self.conn.execute(
            """
            INSERT INTO files (path, lang, service_id, content_hash, size_bytes,
                               parse_status, parse_errors, skip_reason, extractors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                lang         = excluded.lang,
                service_id   = excluded.service_id,
                content_hash = excluded.content_hash,
                size_bytes   = excluded.size_bytes,
                parse_status = excluded.parse_status,
                parse_errors = excluded.parse_errors,
                skip_reason  = excluded.skip_reason,
                extractors   = excluded.extractors
            RETURNING id
            """,
            (
                record.path,
                record.lang,
                record.service_id,
                record.content_hash,
                record.size_bytes,
                record.parse_status.value,
                dumps([{"line": e.line, "col": e.col, "message": e.message}
                       for e in record.parse_errors]),
                record.skip_reason.value if record.skip_reason else None,
                dumps([{"name": e.name, "version": e.version, "tier": e.tier.value}
                       for e in record.extractors]) or "[]",
            ),
        )
        return int(cur.fetchone()[0])

    def write_nodes(self, nodes: Sequence[Node], *, file_id: int | None = None) -> None:
        """Persist nodes and mirror them into the FTS index.

        FTS is maintained here rather than by a trigger. Triggers would fire per
        row during a bulk insert; more importantly, this module is the only
        writer, so there is no path that could bypass the mirror and no need to
        defend against one.
        """
        if not nodes:
            return
        ordered = sorted(nodes, key=lambda n: n.id)
        rows = [
            (
                n.id, n.kind.value, n.name, n.qualified_name, n.lang,
                file_id if file_id is not None else n.file_id, n.file_path,
                n.service_id, n.signature, n.signature_hash, n.docstring,
                n.return_type, n.type_ref,
                n.visibility.value if n.visibility else None,
                int(n.flags),
                dumps(list(n.type_params)), dumps(list(n.decorators)),
                n.parent_id, *n.span.as_row(),
            )
            for n in ordered
        ]
        self.conn.executemany(
            """
            INSERT INTO nodes (
                id, kind, name, qualified_name, lang, file_id, file_path, service_id,
                signature, signature_hash, docstring, return_type, type_ref, visibility,
                flags, type_params, decorators, parent_id,
                start_byte, end_byte, start_line, start_col, end_line, end_col
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                signature      = COALESCE(excluded.signature, nodes.signature),
                docstring      = COALESCE(excluded.docstring, nodes.docstring),
                return_type    = COALESCE(excluded.return_type, nodes.return_type),
                type_ref       = COALESCE(excluded.type_ref, nodes.type_ref),
                visibility     = COALESCE(excluded.visibility, nodes.visibility),
                flags          = nodes.flags | excluded.flags,
                type_params    = COALESCE(excluded.type_params, nodes.type_params),
                decorators     = COALESCE(excluded.decorators, nodes.decorators),
                parent_id      = COALESCE(excluded.parent_id, nodes.parent_id)
            """,
            rows,
        )
        # A node observed by both Tier A and a Tier B oracle collides by design
        # (§6 guarantees convergence), so the FTS mirror must not double-insert.
        self.conn.executemany(
            "DELETE FROM nodes_fts WHERE node_id = ?", [(n.id,) for n in ordered]
        )
        self.conn.executemany(
            "INSERT INTO nodes_fts (node_id, name, qualified_name, docstring, signature) "
            "VALUES (?, ?, ?, ?, ?)",
            [(n.id, n.name, n.qualified_name, n.docstring or "", n.signature or "")
             for n in ordered],
        )

    def write_edges(self, edges: Sequence[Edge], *, file_id: int | None = None) -> int:
        """Persist edges, skipping exact duplicates. Returns the number written.

        ``INSERT OR IGNORE`` against ``idx_edges_identity``: re-resolving one
        file must be idempotent, and two genuinely distinct call sites differ in
        their span so they survive as separate rows.
        """
        if not edges:
            return 0
        ordered = sorted(edges, key=lambda e: e.identity)
        rows = [
            (
                e.src, e.dst, e.kind.value,
                file_id if file_id is not None else e.file_id, e.file_path,
                e.evidence.tier.value, e.evidence.label, e.evidence.confidence,
                e.resolution.value, e.provenance, dumps(e.metadata),
                e.span.start_byte if e.span else None,
                e.span.end_byte if e.span else None,
                e.span.start_line if e.span else None,
                e.span.start_col if e.span else None,
            )
            for e in ordered
        ]
        cur = self.conn.executemany(
            """
            INSERT OR IGNORE INTO edges (
                src, dst, kind, file_id, file_path,
                evidence_tier, evidence_label, confidence,
                resolution, provenance, metadata,
                start_byte, end_byte, line, col
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def write_unresolved(self, refs: Sequence[UnresolvedRef], *, file_id: int | None = None) -> None:
        if not refs:
            return
        ordered = sorted(refs, key=lambda r: (r.file_path, r.span.start_byte, r.ref_text))
        self.conn.executemany(
            """
            INSERT INTO unresolved_refs (
                from_node, file_id, file_path, ref_text, ref_kind, receiver_hint,
                candidates, status, reason, metadata,
                start_byte, end_byte, line, col
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.from_node, file_id if file_id is not None else r.file_id, r.file_path,
                    r.ref_text, r.ref_kind.value, r.receiver_hint,
                    dumps(list(r.candidates)), r.status.value, r.reason, dumps(r.metadata),
                    r.span.start_byte, r.span.end_byte, r.span.start_line, r.span.start_col,
                )
                for r in ordered
            ],
        )

    def write_boundaries(self, boundaries: Sequence[Boundary]) -> None:
        if not boundaries:
            return
        ordered = sorted(boundaries, key=lambda b: (b.node_id, b.kind.value, b.detail or ""))
        self.conn.executemany(
            "INSERT INTO boundaries (node_id, kind, detail, candidates, line, col) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    b.node_id, b.kind.value, b.detail, dumps(list(b.candidates)),
                    b.span.start_line if b.span else None,
                    b.span.start_col if b.span else None,
                )
                for b in ordered
            ],
        )

    def write_coverage(self, coverage: Coverage, file_id: int) -> None:
        self.write_coverages([(coverage, file_id)])

    def write_coverages(self, entries: Sequence[tuple[Coverage, int]]) -> None:
        """Batch form. One statement rather than one per file."""
        if not entries:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO coverage (
                file_id, nodes_found, refs_total, refs_exact, refs_inferred,
                refs_unresolved, boundaries_count, dataflow_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sorted(
                (
                    file_id, c.nodes_found, c.refs_total, c.refs_exact,
                    c.refs_inferred, c.refs_unresolved, c.boundaries_count,
                    c.dataflow_status.value,
                )
                for c, file_id in entries
            ),
        )

    def write_aliases(self, aliases: Iterable[tuple[str, str, str]]) -> None:
        """Record ``(node_id, engine, engine_symbol_id)`` triples from Tier B."""
        rows = sorted(set(aliases), key=lambda r: (r[1], r[2]))
        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO node_aliases (node_id, engine, engine_symbol_id) "
                "VALUES (?, ?, ?)",
                rows,
            )

    def purge_file(self, path: str) -> None:
        """Remove everything derived from one file, for incremental reindex.

        Nodes cascade to their edges, unresolved refs and boundaries via foreign
        keys. Edges *into* this file's nodes from elsewhere also cascade, so the
        caller must re-resolve any file that referenced this one -- which is what
        the content-hash dependency check in the indexer decides.
        """
        row = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if row is None:
            return
        file_id = row["id"]
        node_ids = [
            r["id"] for r in self.conn.execute(
                "SELECT id FROM nodes WHERE file_id = ?", (file_id,)
            )
        ]
        self.conn.executemany("DELETE FROM nodes_fts WHERE node_id = ?", [(n,) for n in node_ids])
        self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    # ---- determinism ------------------------------------------------------

    def derive_entry_point_flags(self) -> int:
        """Set ``NodeFlags.ENTRY_POINT`` on every source of an ``EXPOSES`` edge.

        ``core/kinds.py`` documents the flag as derived -- "it mirrors the
        existence of an EXPOSES edge, and exists so the hot 'is this an entry
        point' check does not need a join" -- and nothing derived it, so it was
        ``0`` on every node while 13 ``EXPOSES`` edges existed (#57).
        ``ImpactReport.entry_points`` is gated on it, so the most consequential
        field in a blast-radius answer was unreachable.

        Done as one set-based statement after edges are written, because
        contracts are detected after nodes are persisted and the flag must not
        be able to disagree with the edge. Not computed lazily per query: the
        flag exists specifically to keep that join off the hot path.

        Returns the number of nodes flagged, so the caller can report it.
        """
        cursor = self.conn.execute(
            # `flags | 256` rather than `= 256`: other flags on the node are
            # already set and must survive.
            """
            UPDATE nodes SET flags = flags | ?
             WHERE id IN (SELECT DISTINCT src FROM edges WHERE kind = ?)
               AND flags & ? = 0
            """,
            (int(NodeFlags.ENTRY_POINT), EdgeKind.EXPOSES.value,
             int(NodeFlags.ENTRY_POINT)),
        )
        return cursor.rowcount or 0

    def nodes_with_flag(self, flag: NodeFlags) -> list[Node]:
        """Every node carrying ``flag``. For assertions and reporting."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE flags & ? ORDER BY id", (int(flag),)
        )
        return [_node_from_row(r) for r in rows]

    def edges_of_kind(self, kind: EdgeKind | str) -> list[Edge]:
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE kind = ? ORDER BY src, dst",
            (kind.value if isinstance(kind, EdgeKind) else str(kind),),
        )
        return [_edge_from_row(r) for r in rows]

    def compute_build_digest(self) -> str:
        """Hash the stored graph, for ``--verify-determinism`` (§11).

        Hashes the *content* of nodes, edges, unresolved refs and boundaries in a
        canonical order -- never rowids, which depend on insertion sequence, nor
        timestamps. Two indexes of one commit must produce the same digest;
        anything order-dependent leaking in would defeat the check it exists for.
        """
        h = hashlib.blake2b(digest_size=32)

        for row in self.conn.execute(
            "SELECT id, kind, name, qualified_name, lang, file_path, service_id, "
            "       signature, signature_hash, docstring, return_type, type_ref, "
            "       visibility, flags, type_params, decorators, parent_id, "
            "       start_byte, end_byte, start_line, start_col, end_line, end_col "
            "FROM nodes ORDER BY id"
        ):
            h.update(b"N")
            h.update("\x00".join("" if v is None else str(v) for v in tuple(row)).encode())

        for row in self.conn.execute(
            "SELECT src, dst, kind, file_path, evidence_tier, evidence_label, confidence, "
            "       resolution, provenance, metadata, start_byte, end_byte, line, col "
            "FROM edges ORDER BY src, dst, kind, IFNULL(start_byte,-1), IFNULL(end_byte,-1)"
        ):
            h.update(b"E")
            h.update("\x00".join("" if v is None else str(v) for v in tuple(row)).encode())

        for row in self.conn.execute(
            "SELECT from_node, file_path, ref_text, ref_kind, receiver_hint, candidates, "
            "       status, reason, start_byte, end_byte "
            "FROM unresolved_refs ORDER BY file_path, start_byte, ref_text, ref_kind"
        ):
            h.update(b"U")
            h.update("\x00".join("" if v is None else str(v) for v in tuple(row)).encode())

        for row in self.conn.execute(
            "SELECT node_id, kind, detail, candidates "
            "FROM boundaries ORDER BY node_id, kind, IFNULL(detail,'')"
        ):
            h.update(b"B")
            h.update("\x00".join("" if v is None else str(v) for v in tuple(row)).encode())

        return h.hexdigest()

    def unclaimed_frameworks(self) -> list[dict[str, Any]]:
        """Frameworks present but unmodelled -- the §8.2 adapter work list.

        Returns an empty list for absent or malformed content rather than
        raising: a query must not fail because a summary field is unreadable,
        and an empty list is the honest answer when we cannot say.
        """
        try:
            row = self.conn.execute(
                "SELECT unclaimed_frameworks FROM graph_meta WHERE id = 1"
            ).fetchone()
        except sqlite3.DatabaseError:
            return []
        if not row or not row["unclaimed_frameworks"]:
            return []
        try:
            payload = loads(row["unclaimed_frameworks"])
        except (ValueError, TypeError):
            logger.debug("unreadable unclaimed_frameworks in %s", self.db_path)
            return []
        return payload if isinstance(payload, list) else []

    def record_determinism(self, ok: bool) -> None:
        """Store the verdict of ``verify --determinism``.

        A graph that has never been verified keeps ``NULL``, which is a third
        state and must not collapse into ``0``: "not checked" and "checked and
        failed" are different facts and only one is a defect.
        """
        self.conn.execute(
            "UPDATE graph_meta SET deterministic_ok = ? WHERE id = 1", (int(ok),)
        )
        self.conn.commit()

    def provenance(self) -> dict[str, object]:
        """Commit identity of this index, for reporting and upload."""
        row = self.conn.execute(
            "SELECT commit_sha, branch, dirty FROM graph_meta WHERE id = 1"
        ).fetchone()
        if row is None:
            return {"commit_sha": None, "branch": None, "dirty": None}
        return {
            "commit_sha": row["commit_sha"],
            "branch": row["branch"],
            "dirty": None if row["dirty"] is None else bool(row["dirty"]),
        }

    def finalise(
        self,
        *,
        grammar_digest: str = "",
        spec_digest: str = "",
        adapter_digest: str = "",
        ontology_digest: str = "",
        unclaimed_frameworks: Sequence[dict[str, Any]] | None = None,
        built_at: str | None = None,
    ) -> str:
        """Record digests and optimise the index. Returns the build digest."""
        digest = self.compute_build_digest()
        self.conn.execute(
            """
            UPDATE graph_meta SET
                build_digest         = ?,
                grammar_digest       = ?,
                spec_digest          = ?,
                adapter_digest       = ?,
                ontology_digest      = ?,
                unclaimed_frameworks = ?,
                built_at             = ?
            WHERE id = 1
            """,
            (digest, grammar_digest, spec_digest, adapter_digest, ontology_digest,
             dumps(list(unclaimed_frameworks)) if unclaimed_frameworks else None,
             built_at),
        )
        self.conn.execute("INSERT INTO nodes_fts (nodes_fts) VALUES ('optimize')")
        self.conn.commit()
        self.conn.execute("ANALYZE")
        self.conn.commit()
        return digest

    # ---- primitive reads --------------------------------------------------
    #
    # Deliberately primitive. Path finding, impact and data-flow closure live in
    # the query layer; this exposes only single-hop and span lookups so the
    # traversal logic has one home.

    def get_node(self, node_id: str) -> Node | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _node_from_row(row) if row else None

    def nodes_by_qualified_name(self, qname: str, sig_hash: str | None = None) -> list[Node]:
        """Look up by human-readable name, optionally disambiguating overloads."""
        if sig_hash is None:
            rows = self.conn.execute(
                "SELECT * FROM nodes WHERE qualified_name = ? ORDER BY id", (qname,)
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM nodes WHERE qualified_name = ? AND signature_hash = ? ORDER BY id",
                (qname, sig_hash),
            )
        return [_node_from_row(r) for r in rows]

    def nodes_containing_line(self, file_path: str, line: int) -> list[Node]:
        """Nodes whose span covers ``line``, innermost last.

        The first half of ``impact_of(file:line)`` (§10.4): the innermost result
        is what is being edited.
        """
        rows = self.conn.execute(
            """
            SELECT n.* FROM nodes n
            JOIN files f ON f.id = n.file_id
            WHERE f.path = ? AND n.start_line <= ? AND n.end_line >= ?
            ORDER BY (n.end_byte - n.start_byte) DESC, n.start_byte ASC
            """,
            (file_path, line, line),
        )
        return [_node_from_row(r) for r in rows]

    def edges_on_line(self, file_path: str, line: int) -> list[Edge]:
        """Edges written on ``line`` -- the operations that line performs.

        The second half of ``impact_of(file:line)``. This is why edges carry
        spans: without it a line could only be mapped to its enclosing symbol,
        losing the distinction between "this line calls save()" and "this line is
        somewhere inside submit()".
        """
        rows = self.conn.execute(
            """
            SELECT e.* FROM edges e
            JOIN files f ON f.id = e.file_id
            WHERE f.path = ? AND e.line = ?
            ORDER BY e.src, e.dst, e.kind
            """,
            (file_path, line),
        )
        return [_edge_from_row(r) for r in rows]

    def edges_from(self, node_id: str, kinds: Iterable[str] | None = None) -> list[Edge]:
        return self._edges_by_endpoint("src", node_id, kinds)

    def edges_to(self, node_id: str, kinds: Iterable[str] | None = None) -> list[Edge]:
        return self._edges_by_endpoint("dst", node_id, kinds)

    def _edges_by_endpoint(
        self, column: Literal["src", "dst"], node_id: str, kinds: Iterable[str] | None
    ) -> list[Edge]:
        # Only the endpoint column name and a count of '?' placeholders are
        # interpolated; every value is bound. The guard makes that checkable at
        # runtime rather than resting on the two internal call sites staying
        # correct, which is what justifies the S608 suppressions below.
        if column not in ("src", "dst"):
            raise ValueError(f"endpoint column must be 'src' or 'dst', got {column!r}")

        kind_list = [str(k) for k in kinds] if kinds else None
        if kind_list:
            placeholders = ",".join("?" * len(kind_list))
            rows = self.conn.execute(
                f"SELECT * FROM edges WHERE {column} = ? AND kind IN ({placeholders}) "  # noqa: S608
                f"ORDER BY src, dst, kind, IFNULL(start_byte,-1)",
                (node_id, *kind_list),
            )
        else:
            rows = self.conn.execute(
                f"SELECT * FROM edges WHERE {column} = ? "  # noqa: S608
                f"ORDER BY src, dst, kind, IFNULL(start_byte,-1)",
                (node_id,),
            )
        return [_edge_from_row(r) for r in rows]

    def search(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        lang: str | None = None,
        service_id: str | None = None,
        limit: int = 30,
    ) -> list[Node]:
        """Ranked full-text search over name, qualified name, docstring, signature.

        BM25-ranked, replacing a linear substring scan that broke at the limit
        and therefore returned the first N matches in dict order.

        Uses AND-then-OR fallback for multi-term queries:
        1. First tries AND semantics (all terms must be in same document)
        2. If AND returns 0 results, falls back to OR (any term matches)
        This prevents multi-term queries from returning 0 hits when terms don't co-occur.
        """
        match = _fts_query(query)
        if match is None:
            return []

        # Build base clauses (kinds, lang, service_id filters)
        base_clauses = []
        base_params: list[Any] = []
        if kinds:
            kind_list = [str(k) for k in kinds]
            base_clauses.append(f"n.kind IN ({','.join('?' * len(kind_list))})")
            base_params.extend(kind_list)
        if lang:
            base_clauses.append("n.lang = ?")
            base_params.append(lang)
        if service_id:
            base_clauses.append("n.service_id = ?")
            base_params.append(service_id)

        # Try AND first (exact multi-term match)
        and_clauses = ["nodes_fts MATCH ?", *base_clauses]
        and_params = [match, *base_params, limit]

        # `clauses` holds only literals defined above; all values are in `params`.
        rows = self.conn.execute(
            f"""
            SELECT n.* FROM nodes_fts
            JOIN nodes n ON n.id = nodes_fts.node_id
            WHERE {' AND '.join(and_clauses)}
            ORDER BY bm25(nodes_fts, 10.0, 5.0, 1.0, 2.0), n.id
            LIMIT ?
            """,  # noqa: S608
            and_params,
        )
        results = [_node_from_row(r) for r in rows]

        # If AND returned 0 results and query has multiple terms, try OR
        if not results and " " in query:
            or_match = _fts_query_or(query)  # Convert to OR semantics
            if or_match:
                or_clauses = ["nodes_fts MATCH ?", *base_clauses]
                or_params = [or_match, *base_params, limit]

                rows = self.conn.execute(
                    f"""
                    SELECT n.* FROM nodes_fts
                    JOIN nodes n ON n.id = nodes_fts.node_id
                    WHERE {' AND '.join(or_clauses)}
                    ORDER BY bm25(nodes_fts, 10.0, 5.0, 1.0, 2.0), n.id
                    LIMIT ?
                    """,  # noqa: S608
                    or_params,
                )
                results = [_node_from_row(r) for r in rows]

        return results

    def unresolved_for_file(self, file_path: str) -> list[UnresolvedRef]:
        rows = self.conn.execute(
            "SELECT * FROM unresolved_refs WHERE file_path = ? ORDER BY start_byte",
            (file_path,),
        )
        return [_ref_from_row(r) for r in rows]

    def boundaries_for_nodes(self, node_ids: Sequence[str]) -> list[Boundary]:
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))  # a count of '?', not values
        rows = self.conn.execute(
            f"SELECT * FROM boundaries WHERE node_id IN ({placeholders}) "  # noqa: S608
            f"ORDER BY node_id, kind",
            tuple(node_ids),
        )
        return [
            Boundary(
                node_id=r["node_id"],
                kind=BoundaryKind(r["kind"]),
                detail=r["detail"],
                candidates=tuple(loads(r["candidates"]) or ()),
            )
            for r in rows
        ]

    def files(self) -> list[FileRecord]:
        """Every file row, including skipped ones -- the completeness ledger (§10.6)."""
        return [
            _file_from_row(r)
            for r in self.conn.execute("SELECT * FROM files ORDER BY path")
        ]

    def file_id_for(self, path: str) -> int | None:
        row = self.conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        return int(row["id"]) if row else None

    def content_hashes(self) -> dict[str, str]:
        """``{path: content_hash}`` for every indexed file, for incremental diff."""
        return {
            r["path"]: r["content_hash"]
            for r in self.conn.execute("SELECT path, content_hash FROM files")
        }

    def counts(self) -> dict[str, int]:
        """Row counts per table, for reporting and acceptance tests."""
        tables = (
            "services", "files", "nodes", "edges",
            "unresolved_refs", "boundaries", "coverage", "node_aliases",
        )
        # `tables` is the literal tuple above; no caller input reaches the SQL.
        return {
            t: int(self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])  # noqa: S608
            for t in tables
        }

    def edge_kind_counts(self) -> dict[str, int]:
        return {
            r["kind"]: r["n"]
            for r in self.conn.execute(
                "SELECT kind, count(*) AS n FROM edges GROUP BY kind ORDER BY n DESC"
            )
        }

    def node_kind_counts(self) -> dict[str, int]:
        return {
            r["kind"]: r["n"]
            for r in self.conn.execute(
                "SELECT kind, count(*) AS n FROM nodes GROUP BY kind ORDER BY n DESC"
            )
        }

    def dangling_edge_count(self) -> int:
        """Edges whose endpoints are not both real nodes.

        Must always be zero -- foreign keys guarantee it. Asserted after every
        build anyway (§16.1), because this is the exact defect that made the
        schema-3 Dubbo graph 100% non-traversable while reporting success, and a
        cheap assertion is worth more than the assumption that FKs were on.
        """
        row = self.conn.execute(
            """
            SELECT count(*) FROM edges e
            WHERE NOT EXISTS (SELECT 1 FROM nodes WHERE id = e.src)
               OR NOT EXISTS (SELECT 1 FROM nodes WHERE id = e.dst)
            """
        ).fetchone()
        return int(row[0])

    # ---- usage sites (new) ------------------------------------------------

    def add_usage_site(
        self,
        symbol_name: str,
        usage_type: str,
        file_id: int,
        file_path: str,
        start_line: int,
        end_line: int,
        start_byte: int,
        end_byte: int,
        calling_symbol_id: str | None = None,
        context_before: str | None = None,
        context_line: str | None = None,
        context_after: str | None = None,
    ) -> None:
        """Record where a symbol is used in the codebase."""
        self.conn.execute(
            """
            INSERT INTO usage_sites (
                symbol_name, usage_type, file_id, file_path,
                start_line, end_line, start_byte, end_byte,
                calling_symbol_id, context_before, context_line, context_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol_name, usage_type, file_id, file_path,
                start_line, end_line, start_byte, end_byte,
                calling_symbol_id, context_before, context_line, context_after,
            ),
        )

    def find_usage(
        self,
        symbol_name: str,
        usage_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find where a symbol is used (useful for external symbols).

        Args:
            symbol_name: Name to search for (e.g., "anthropic.Anthropic")
            usage_type: Filter by usage type (import, call, etc.). None = all.
            limit: Max results.

        Returns:
            List of usage dicts with symbol_name, usage_type, file_path, line, context.
        """
        if usage_type:
            rows = self.conn.execute(
                """
                SELECT * FROM usage_sites
                WHERE symbol_name = ? AND usage_type = ?
                ORDER BY file_path, start_line
                LIMIT ?
                """,
                (symbol_name, usage_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM usage_sites
                WHERE symbol_name = ?
                ORDER BY file_path, start_line
                LIMIT ?
                """,
                (symbol_name, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    def get_usage_context(
        self,
        file_path: str,
        line_number: int,
        context_lines: int = 3,
    ) -> str:
        """Get source code context around a usage site.

        Returns verbatim source with line numbers.
        """
        try:
            with open(file_path, encoding='utf-8') as f:
                lines = f.readlines()
        except (FileNotFoundError, UnicodeDecodeError):
            return ""

        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)

        result = []
        for i in range(start, end):
            if i < len(lines):
                marker = "→ " if i == line_number - 1 else "  "
                result.append(f"{marker}{i+1:4d} | {lines[i].rstrip()}")

        return "\n".join(result)

    def find_imports(self, symbol_name: str, limit: int = 30) -> list[dict[str, Any]]:
        """Find all import statements for a symbol."""
        return self.find_usage(symbol_name, usage_type="import", limit=limit)

    def find_calls(self, symbol_name: str, limit: int = 30) -> list[dict[str, Any]]:
        """Find all call sites for a symbol."""
        return self.find_usage(symbol_name, usage_type="call", limit=limit)


# ---------------------------------------------------------------------------
# row -> record
# ---------------------------------------------------------------------------

def _node_from_row(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        kind=NodeKind(row["kind"]),
        name=row["name"],
        qualified_name=row["qualified_name"],
        lang=row["lang"],
        span=Span.from_row(row),
        file_path=row["file_path"],
        service_id=row["service_id"],
        signature=row["signature"],
        signature_hash=row["signature_hash"] or "",
        docstring=row["docstring"],
        return_type=row["return_type"],
        type_ref=row["type_ref"],
        visibility=Visibility(row["visibility"]) if row["visibility"] else None,
        flags=NodeFlags(row["flags"]),
        type_params=tuple(loads(row["type_params"]) or ()),
        decorators=tuple(loads(row["decorators"]) or ()),
        parent_id=row["parent_id"],
        file_id=row["file_id"],
    )


def _edge_from_row(row: sqlite3.Row) -> Edge:
    span = None
    if row["start_byte"] is not None:
        span = Span(
            start_byte=row["start_byte"],
            end_byte=row["end_byte"] if row["end_byte"] is not None else row["start_byte"],
            start_line=row["line"] or 0,
            start_col=row["col"] or 0,
            end_line=row["line"] or 0,
            end_col=row["col"] or 0,
        )
    return Edge(
        src=row["src"],
        dst=row["dst"],
        kind=EdgeKind(row["kind"]),
        evidence=Evidence(
            tier=EvidenceTier(row["evidence_tier"]),
            label=row["evidence_label"],
            confidence=row["confidence"],
        ),
        resolution=Resolution(row["resolution"]),
        provenance=row["provenance"],
        span=span,
        file_path=row["file_path"],
        metadata=loads(row["metadata"]) or {},
        file_id=row["file_id"],
    )


def _ref_from_row(row: sqlite3.Row) -> UnresolvedRef:
    return UnresolvedRef(
        from_node=row["from_node"],
        ref_text=row["ref_text"],
        ref_kind=RefKind(row["ref_kind"]),
        span=Span(
            start_byte=row["start_byte"],
            end_byte=row["end_byte"],
            start_line=row["line"],
            start_col=row["col"],
            end_line=row["line"],
            end_col=row["col"],
        ),
        file_path=row["file_path"],
        receiver_hint=row["receiver_hint"],
        candidates=tuple(loads(row["candidates"]) or ()),
        status=RefStatus(row["status"]),
        reason=row["reason"],
        metadata=loads(row["metadata"]) or {},
        file_id=row["file_id"],
    )


def _file_from_row(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        path=row["path"],
        lang=row["lang"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"],
        parse_status=ParseStatus(row["parse_status"]),
        service_id=row["service_id"],
        parse_errors=tuple(
            ParseError(line=e["line"], col=e["col"], message=e["message"])
            for e in (loads(row["parse_errors"]) or ())
        ),
        skip_reason=SkipReason(row["skip_reason"]) if row["skip_reason"] else None,
        extractors=tuple(
            ExtractorRun(name=e["name"], version=e["version"], tier=Tier(e["tier"]))
            for e in (loads(row["extractors"]) or ())
        ),
        id=row["id"],
    )


def _fts_query(text: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH expression with AND semantics.

    FTS5 treats ``-``, ``*``, ``"``, ``:``, ``(``, ``)`` and ``^`` as syntax, so
    an identifier like ``get-user`` or ``foo:bar`` is a parse error rather than a
    search. Those characters are replaced by token separators, which both makes
    the query safe and matches how the ``unicode61`` tokeniser split the indexed
    text in the first place -- ``get-user`` was stored as ``get`` + ``user``.

    A prefix wildcard is appended outside the quotes, which is what a symbol
    search wants: ``load_gr`` should find ``load_graph``.

    Multiple tokens are AND'ed together (default FTS5 behavior).

    Returns ``None`` when nothing searchable remains. There is no MATCH
    expression that reliably means "match nothing" -- ``""`` is a syntax error
    and a NUL sentinel truncates SQLite's C string -- so the caller skips the
    query instead.
    """
    separators = str.maketrans(dict.fromkeys(':-*()^"', " "))
    tokens = [t for t in text.translate(separators).split() if t]
    if not tokens:
        return None
    return " ".join(f'"{t}"*' for t in tokens)


def _fts_query_or(text: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH expression with OR semantics.

    Like _fts_query, but tokens are OR'ed together instead of AND'ed.
    Used as a fallback when AND semantics return 0 results.

    Example:
        "ServiceConfig export service" → ("ServiceConfig"* OR "export"* OR "service"*)

    This allows multi-term queries to match documents containing ANY of the terms,
    not requiring ALL terms to co-occur.
    """
    separators = str.maketrans(dict.fromkeys(':-*()^"', " "))
    tokens = [t for t in text.translate(separators).split() if t]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in tokens)
