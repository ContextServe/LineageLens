"""Incremental differential code graph analyzer for LineageLens."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .config import ProjectConfig
from .db import SQLiteIndexDB
from .model import CodeGraph
from .treesitter_analyzer import EXTENSION_LANG_MAP, TreeSitterAnalyzer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class IncrementalAnalyzer:
    """Performs single-file AST differential updates against SQLiteIndexDB."""

    def __init__(self, config: ProjectConfig | None = None) -> None:
        self.config = config or ProjectConfig()

    def sync_file(
        self,
        project_root: Path,
        rel_file_path: str,
        db: SQLiteIndexDB,
    ) -> bool:
        """Incrementally sync a single file into the SQLite index database.

        Returns True if successful, False if file was ignored or failed.
        """
        abs_path = project_root / rel_file_path

        # 1. Handle file deletion
        if not abs_path.exists():
            logger.info(f"Purging deleted file from index: {rel_file_path}")
            db.purge_file(rel_file_path)
            return True

        # 2. Check file extension & exclusions
        ext = abs_path.suffix.lower()
        if ext not in EXTENSION_LANG_MAP and ext != ".py":
            return False

        try:
            content = abs_path.read_bytes()
        except Exception as exc:
            logger.warning(f"Could not read {rel_file_path}: {exc}")
            return False

        mtime = abs_path.stat().st_mtime
        file_hash = hashlib.sha256(content).hexdigest()
        lang = EXTENSION_LANG_MAP.get(ext, "python")

        # 3. Analyze single file using Tree-sitter analyzer or standard analyzer
        single_file_graph = CodeGraph(project_root=str(project_root))
        
        ts_analyzer = TreeSitterAnalyzer(self.config)
        ts_analyzer._analyze_file(abs_path, rel_file_path, lang, single_file_graph)
        ts_analyzer._build_containers(single_file_graph)
        ts_analyzer._resolve_call_relations(single_file_graph)

        symbols = list(single_file_graph.symbols.values())
        relations = single_file_graph.relations
        containers = list(single_file_graph.containers.values())

        # 4. Upsert single file graph into SQLite DB
        db.upsert_file_graph(
            rel_file_path=rel_file_path,
            lang=lang,
            mtime=mtime,
            file_hash=file_hash,
            symbols=symbols,
            relations=relations,
            containers=containers,
        )

        logger.debug(
            f"Synced {rel_file_path}: {len(symbols)} symbols, {len(relations)} relations"
        )
        return True
