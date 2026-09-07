"""SQLite Index Storage Engine for LineageLens (.lineagelens/index.sqlite)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .model import SCHEMA_VERSION, CodeGraph, Container, Evidence, Relation, ResiliencySignal, Symbol

logger = logging.getLogger(__name__)

DB_NAME = "index.sqlite"

SCHEMA_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS graph_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL DEFAULT 3,
    ontology_version TEXT NOT NULL DEFAULT '1.0',
    project_root TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    lang TEXT NOT NULL,
    mtime REAL NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    module TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER,
    parent_id TEXT,
    resiliency TEXT,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS containers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_id TEXT,
    children TEXT
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    file_id INTEGER NOT NULL,
    line INTEGER NOT NULL,
    evidence_tier TEXT NOT NULL,
    evidence_label TEXT NOT NULL,
    resolution TEXT NOT NULL,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reachability_cache (
    symbol_id TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    rescue_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
CREATE INDEX IF NOT EXISTS idx_relations_target_tier ON relations(target_id, evidence_tier);
"""


class SQLiteIndexDB:
    """Manages atomic reads/writes to .lineagelens/index.sqlite."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def init_db(self) -> None:
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.executescript(SCHEMA_DDL)
            conn.commit()

    def purge_file(self, rel_file_path: str) -> None:
        """Purge all symbols and relations belonging to rel_file_path."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM files WHERE path = ?", (rel_file_path,))
            conn.commit()

    def upsert_file_graph(
        self,
        rel_file_path: str,
        lang: str,
        mtime: float,
        file_hash: str,
        symbols: Iterable[Symbol],
        relations: Iterable[Relation],
        containers: Iterable[Container] | None = None,
    ) -> None:
        """Atomically update index for a single file."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM files WHERE path = ?", (rel_file_path,))

            cursor.execute(
                "INSERT INTO files (path, lang, mtime, hash) VALUES (?, ?, ?, ?)",
                (rel_file_path, lang, mtime, file_hash),
            )
            file_id = cursor.lastrowid

            symbol_rows = []
            for s in symbols:
                resiliency_json = (
                    json.dumps(
                        [
                            {
                                "category": r.category,
                                "severity": r.severity,
                                "line": r.line,
                                "evidence": {
                                    "tier": r.evidence.tier,
                                    "label": r.evidence.label,
                                    "confidence": r.evidence.confidence,
                                },
                            }
                            for r in s.resiliency
                        ]
                    )
                    if s.resiliency
                    else None
                )
                symbol_rows.append(
                    (
                        s.id,
                        file_id,
                        s.name,
                        s.kind,
                        s.module,
                        s.line,
                        s.end_line,
                        s.parent,
                        resiliency_json,
                    )
                )

            cursor.executemany(
                """
                INSERT OR REPLACE INTO symbols (id, file_id, name, kind, module, line, end_line, parent_id, resiliency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                symbol_rows,
            )

            if containers:
                container_rows = [
                    (c.id, c.name, c.kind, c.parent, json.dumps(c.children))
                    for c in containers
                ]
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO containers (id, name, kind, parent_id, children)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    container_rows,
                )

            relation_rows = [
                (
                    r.source,
                    r.target,
                    r.kind,
                    file_id,
                    r.line,
                    r.evidence.tier if r.evidence else "deterministic_fact",
                    r.evidence.label if r.evidence else "static_ast",
                    r.resolution,
                )
                for r in relations
            ]
            cursor.executemany(
                """
                INSERT INTO relations (source_id, target_id, kind, file_id, line, evidence_tier, evidence_label, resolution)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                relation_rows,
            )

            cursor.execute("DELETE FROM reachability_cache")
            conn.commit()

    def save_code_graph(self, graph: CodeGraph) -> None:
        """Bulk save full CodeGraph object into SQLite database."""
        file_symbols: dict[str, list[Symbol]] = {}
        for sym in graph.symbols.values():
            file_symbols.setdefault(sym.file, []).append(sym)

        file_relations: dict[str, list[Relation]] = {}
        for rel in graph.relations:
            file_relations.setdefault(rel.file, []).append(rel)

        all_files = set(file_symbols.keys()) | set(file_relations.keys())

        with self.get_connection() as conn:
            conn.execute("DELETE FROM graph_metadata")
            conn.execute(
                "INSERT INTO graph_metadata (schema_version, ontology_version, project_root) VALUES (?, ?, ?)",
                (SCHEMA_VERSION, graph.ontology_version, graph.project_root),
            )
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM containers")
            conn.execute("DELETE FROM reachability_cache")

            for rel_file in all_files:
                ext = Path(rel_file).suffix
                mtime = time.time()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO files (path, lang, mtime, hash) VALUES (?, ?, ?, ?)",
                    (rel_file, ext, mtime, "bulk"),
                )
                file_id = cursor.lastrowid

                syms = file_symbols.get(rel_file, [])
                symbol_rows = []
                for s in syms:
                    resiliency_json = (
                        json.dumps(
                            [
                                {
                                    "category": r.category,
                                    "severity": r.severity,
                                    "line": r.line,
                                    "evidence": {
                                        "tier": r.evidence.tier,
                                        "label": r.evidence.label,
                                        "confidence": r.evidence.confidence,
                                    },
                                }
                                for r in s.resiliency
                            ]
                        )
                        if s.resiliency
                        else None
                    )
                    symbol_rows.append(
                        (
                            s.id,
                            file_id,
                            s.name,
                            s.kind,
                            s.module,
                            s.line,
                            s.end_line,
                            s.parent,
                            resiliency_json,
                        )
                    )

                cursor.executemany(
                    """
                    INSERT INTO symbols (id, file_id, name, kind, module, line, end_line, parent_id, resiliency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    symbol_rows,
                )

                rels = file_relations.get(rel_file, [])
                cursor.executemany(
                    """
                    INSERT INTO relations (source_id, target_id, kind, file_id, line, evidence_tier, evidence_label, resolution)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            r.source,
                            r.target,
                            r.kind,
                            file_id,
                            r.line,
                            r.evidence.tier if r.evidence else "deterministic_fact",
                            r.evidence.label if r.evidence else "static_ast",
                            r.resolution,
                        )
                        for r in rels
                    ],
                )

            if graph.containers:
                conn.executemany(
                    """
                    INSERT INTO containers (id, name, kind, parent_id, children)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (c.id, c.name, c.kind, c.parent, json.dumps(c.children))
                        for c in graph.containers.values()
                    ],
                )

            conn.commit()

    def to_code_graph(self, project_root: str = ".") -> CodeGraph:
        """Load CodeGraph model from SQLite database."""
        from .queries import GraphNotFoundError

        with self.get_connection() as conn:
            meta = conn.execute("SELECT * FROM graph_metadata").fetchone()
            if meta:
                found_version = meta["schema_version"]
                if found_version != SCHEMA_VERSION:
                    raise GraphNotFoundError(
                        f"index.sqlite schema_version mismatch (found v{found_version}, expected v{SCHEMA_VERSION})"
                    )
                project_root = meta["project_root"] or project_root

            graph = CodeGraph(project_root=project_root)

            cursor = conn.cursor()
            file_map = {
                r["id"]: r["path"]
                for r in cursor.execute("SELECT id, path FROM files")
            }

            for row in conn.execute("SELECT * FROM symbols"):
                resiliency_list = []
                if row["resiliency"]:
                    try:
                        res_data = json.loads(row["resiliency"])
                        for r in res_data:
                            ev = Evidence(
                                tier=r["evidence"]["tier"],
                                label=r["evidence"]["label"],
                                confidence=r["evidence"].get("confidence"),
                            )
                            resiliency_list.append(
                                ResiliencySignal(
                                    category=r["category"],
                                    severity=r["severity"],
                                    evidence=ev,
                                    line=r["line"],
                                )
                            )
                    except Exception as exc:
                        logger.debug(f"Failed to parse resiliency JSON: {exc}")

                rel_file = file_map.get(row["file_id"], row["module"].replace(".", "/") + ".py")
                sym = Symbol(
                    id=row["id"],
                    name=row["name"],
                    kind=row["kind"],
                    file=rel_file,
                    line=row["line"],
                    end_line=row["end_line"],
                    module=row["module"],
                    parent=row["parent_id"],
                    resiliency=resiliency_list,
                )
                graph.add_symbol(sym)

            for row in conn.execute("SELECT * FROM containers"):
                children_list = []
                if row["children"]:
                    try:
                        children_list = json.loads(row["children"])
                    except Exception:
                        pass
                c = Container(
                    id=row["id"],
                    name=row["name"],
                    kind=row["kind"],
                    file=None,
                    parent=row["parent_id"],
                    children=children_list,
                )
                graph.add_container(c)

            for row in conn.execute("SELECT * FROM relations"):
                rel_file = file_map.get(row["file_id"], "")
                r = Relation(
                    source=row["source_id"],
                    target=row["target_id"],
                    kind=row["kind"],
                    file=rel_file,
                    line=row["line"],
                    evidence=Evidence(
                        tier=row["evidence_tier"], label=row["evidence_label"]
                    ),
                    resolution=row["resolution"],
                )
                graph.add_relation(r)

        return graph

    def get_symbol(self, symbol_id: str) -> Symbol | None:
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT s.*, f.path as file_path 
                FROM symbols s 
                JOIN files f ON s.file_id = f.id 
                WHERE s.id = ?
                """,
                (symbol_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            resiliency_list = []
            if row["resiliency"]:
                try:
                    res_data = json.loads(row["resiliency"])
                    for r in res_data:
                        ev = Evidence(
                            tier=r["evidence"]["tier"],
                            label=r["evidence"]["label"],
                            confidence=r["evidence"].get("confidence"),
                        )
                        resiliency_list.append(
                            ResiliencySignal(
                                category=r["category"],
                                severity=r["severity"],
                                evidence=ev,
                                line=r["line"],
                            )
                        )
                except Exception:
                    pass
            return Symbol(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                file=row["file_path"],
                line=row["line"],
                end_line=row["end_line"],
                module=row["module"],
                parent=row["parent_id"],
                resiliency=resiliency_list,
            )

    def search_symbols(
        self,
        query: str = "",
        kind: str | None = None,
        module: str | None = None,
    ) -> list[Symbol]:
        sql = """
        SELECT s.*, f.path as file_path 
        FROM symbols s 
        JOIN files f ON s.file_id = f.id 
        WHERE 1=1
        """
        params: list[Any] = []

        if query:
            sql += " AND (s.name LIKE ? OR s.id LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])

        if kind:
            sql += " AND s.kind = ?"
            params.append(kind)

        if module:
            sql += " AND s.module LIKE ?"
            params.append(f"%{module}%")

        results: list[Symbol] = []
        with self.get_connection() as conn:
            for row in conn.execute(sql, params):
                resiliency_list = []
                if row["resiliency"]:
                    try:
                        res_data = json.loads(row["resiliency"])
                        for r in res_data:
                            ev = Evidence(
                                tier=r["evidence"]["tier"],
                                label=r["evidence"]["label"],
                                confidence=r["evidence"].get("confidence"),
                            )
                            resiliency_list.append(
                                ResiliencySignal(
                                    category=r["category"],
                                    severity=r["severity"],
                                    evidence=ev,
                                    line=r["line"],
                                )
                            )
                    except Exception:
                        pass
                results.append(
                    Symbol(
                        id=row["id"],
                        name=row["name"],
                        kind=row["kind"],
                        file=row["file_path"],
                        line=row["line"],
                        end_line=row["end_line"],
                        module=row["module"],
                        parent=row["parent_id"],
                        resiliency=resiliency_list,
                    )
                )
        return results

    def get_callers(
        self, target_id: str, evidence_tier: str | None = None
    ) -> list[Relation]:
        sql = """
        SELECT r.*, f.path as file_path 
        FROM relations r 
        JOIN files f ON r.file_id = f.id 
        WHERE r.target_id = ?
        """
        params: list[Any] = [target_id]

        if evidence_tier:
            sql += " AND r.evidence_tier = ?"
            params.append(evidence_tier)

        results: list[Relation] = []
        with self.get_connection() as conn:
            for row in conn.execute(sql, params):
                results.append(
                    Relation(
                        source=row["source_id"],
                        target=row["target_id"],
                        kind=row["kind"],
                        file=row["file_path"],
                        line=row["line"],
                        evidence=Evidence(
                            tier=row["evidence_tier"],
                            label=row["evidence_label"],
                        ),
                        resolution=row["resolution"],
                    )
                )
        return results

    def get_callees(
        self, source_id: str, evidence_tier: str | None = None
    ) -> list[Relation]:
        sql = """
        SELECT r.*, f.path as file_path 
        FROM relations r 
        JOIN files f ON r.file_id = f.id 
        WHERE r.source_id = ?
        """
        params: list[Any] = [source_id]

        if evidence_tier:
            sql += " AND r.evidence_tier = ?"
            params.append(evidence_tier)

        results: list[Relation] = []
        with self.get_connection() as conn:
            for row in conn.execute(sql, params):
                results.append(
                    Relation(
                        source=row["source_id"],
                        target=row["target_id"],
                        kind=row["kind"],
                        file=row["file_path"],
                        line=row["line"],
                        evidence=Evidence(
                            tier=row["evidence_tier"],
                            label=row["evidence_label"],
                        ),
                        resolution=row["resolution"],
                    )
                )
        return results
