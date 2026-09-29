"""Local SQLite metrics persistence for MCP tool invocations and token savings.

Stores invocation telemetry, counterfactual baseline tokens, optimized tokens,
and cloud synchronization state in `.lineagelens/metrics.sqlite`.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

METRICS_DB_FILENAME = "metrics.sqlite"

METRICS_SCHEMA_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS mcp_invocations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,
    tool_name         TEXT NOT NULL,
    query_type        TEXT NOT NULL,
    raw_tokens        INTEGER NOT NULL,
    optimized_tokens  INTEGER NOT NULL,
    tokens_saved      INTEGER NOT NULL,
    duration_ms       INTEGER NOT NULL,
    response_bytes    INTEGER NOT NULL,
    status            TEXT NOT NULL DEFAULT 'ok',
    error_message     TEXT,
    intent            TEXT,
    result_count      INTEGER DEFAULT 0,
    truncated         INTEGER DEFAULT 0,
    repo_name         TEXT NOT NULL,
    branch            TEXT,
    commit_sha        TEXT,
    model_name        TEXT NOT NULL DEFAULT 'gpt-4o',
    synced_to_cloud   INTEGER NOT NULL DEFAULT 0,
    synced_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_mcp_invocations_timestamp ON mcp_invocations(timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_tool ON mcp_invocations(tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_repo ON mcp_invocations(repo_name);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_sync ON mcp_invocations(synced_to_cloud);
"""

PRICING_MODELS: dict[str, float] = {
    "gpt-4o": 5.00,
    "claude-3-5-sonnet": 6.00,
    "o1": 10.00,
    "o3-mini": 10.00,
    "deepseek-r1": 1.00,
    "deepseek-v3": 1.00,
}


@dataclass(slots=True)
class MetricRecord:
    tool_name: str
    query_type: str
    raw_tokens: int
    optimized_tokens: int
    tokens_saved: int
    duration_ms: int
    response_bytes: int
    repo_name: str
    timestamp: str | None = None
    status: str = "ok"
    error_message: str | None = None
    intent: str | None = None
    result_count: int = 0
    truncated: int = 0
    branch: str | None = None
    commit_sha: str | None = None
    model_name: str = "gpt-4o"
    synced_to_cloud: int = 0
    synced_at: str | None = None


class MetricsStore:
    """Manages the metrics SQLite database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).resolve()
        self._init_db()

    @classmethod
    def for_project(cls, project_root: Path) -> MetricsStore:
        db_path = Path(project_root).resolve() / ".lineagelens" / METRICS_DB_FILENAME
        return cls(db_path)

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            check_same_thread=False,
            isolation_level=None,  # autocommit mode
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(METRICS_SCHEMA_DDL)

    def record(self, record: MetricRecord) -> int:
        """Insert a single invocation record."""
        ts = record.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO mcp_invocations (
                    timestamp, tool_name, query_type, raw_tokens, optimized_tokens,
                    tokens_saved, duration_ms, response_bytes, status, error_message,
                    intent, result_count, truncated, repo_name, branch, commit_sha,
                    model_name, synced_to_cloud, synced_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    ts,
                    record.tool_name,
                    record.query_type,
                    record.raw_tokens,
                    record.optimized_tokens,
                    record.tokens_saved,
                    record.duration_ms,
                    record.response_bytes,
                    record.status,
                    record.error_message,
                    record.intent,
                    record.result_count,
                    1 if record.truncated else 0,
                    record.repo_name,
                    record.branch,
                    record.commit_sha,
                    record.model_name,
                    record.synced_to_cloud,
                    record.synced_at,
                ),
            )
            return cur.lastrowid or 0

    def _period_clause(self, period: str) -> tuple[str, list[Any]]:
        clauses = {
            "today": "WHERE timestamp >= datetime('now', 'start of day')",
            "7d": "WHERE timestamp >= datetime('now', '-7 days')",
            "30d": "WHERE timestamp >= datetime('now', '-30 days')",
        }
        return clauses.get(period, ""), []

    def get_summary(self, period: str = "all", model_name: str = "gpt-4o") -> dict[str, Any]:
        """Aggregate totals across the specified time period."""
        clause, params = self._period_clause(period)
        sql = f"""
            SELECT
                COUNT(*) as total_invocations,
                COALESCE(SUM(raw_tokens), 0) as raw_tokens,
                COALESCE(SUM(optimized_tokens), 0) as optimized_tokens,
                COALESCE(SUM(tokens_saved), 0) as tokens_saved,
                COALESCE(AVG(duration_ms), 0.0) as avg_duration_ms
            FROM mcp_invocations
            {clause}
        """  # noqa: S608
        with self._get_connection() as conn:
            row = conn.execute(sql, params).fetchone()
            total_invocations = int(row["total_invocations"])
            raw_tokens = int(row["raw_tokens"])
            optimized_tokens = int(row["optimized_tokens"])
            tokens_saved = int(row["tokens_saved"])
            avg_duration_ms = round(float(row["avg_duration_ms"]), 1)

        reduction_percentage = (
            round((tokens_saved / raw_tokens) * 100.0, 2) if raw_tokens > 0 else 0.0
        )
        rate = PRICING_MODELS.get(model_name, PRICING_MODELS["gpt-4o"])
        cost_savings_usd = round((tokens_saved / 1_000_000) * rate, 2)

        return {
            "total_invocations": total_invocations,
            "raw_tokens": raw_tokens,
            "optimized_tokens": optimized_tokens,
            "tokens_saved": tokens_saved,
            "reduction_percentage": reduction_percentage,
            "cost_savings_usd": cost_savings_usd,
            "model_name": model_name,
            "avg_duration_ms": avg_duration_ms,
        }

    def get_by_tool(self, period: str = "all") -> dict[str, dict[str, Any]]:
        """Tool-by-tool breakdown across the specified time period."""
        clause, params = self._period_clause(period)
        sql = f"""
            SELECT
                tool_name,
                COUNT(*) as calls,
                COALESCE(SUM(raw_tokens), 0) as raw_tokens,
                COALESCE(SUM(optimized_tokens), 0) as optimized_tokens,
                COALESCE(SUM(tokens_saved), 0) as tokens_saved,
                COALESCE(AVG(duration_ms), 0.0) as avg_duration_ms
            FROM mcp_invocations
            {clause}
            GROUP BY tool_name
            ORDER BY calls DESC, tool_name ASC
        """  # noqa: S608
        results: dict[str, dict[str, Any]] = {}
        with self._get_connection() as conn:
            for row in conn.execute(sql, params):
                raw = int(row["raw_tokens"])
                opt = int(row["optimized_tokens"])
                saved = int(row["tokens_saved"])
                red = round((saved / raw) * 100.0, 2) if raw > 0 else 0.0
                results[row["tool_name"]] = {
                    "calls": int(row["calls"]),
                    "raw_tokens": raw,
                    "optimized_tokens": opt,
                    "tokens_saved": saved,
                    "reduction_percentage": red,
                    "avg_duration_ms": round(float(row["avg_duration_ms"]), 1),
                }
        return results

    def get_cloud_sync_status(self) -> dict[str, Any]:
        """Status of synced vs unsynced records."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN synced_to_cloud = 1 THEN 1 ELSE 0 END), 0) as synced,
                    COALESCE(SUM(CASE WHEN synced_to_cloud = 0 THEN 1 ELSE 0 END), 0) as pending,
                    MAX(CASE WHEN synced_to_cloud = 1 THEN synced_at ELSE NULL END) as last_synced_at
                FROM mcp_invocations
                """
            ).fetchone()
            return {
                "total": int(row["total"]),
                "synced": int(row["synced"]),
                "pending": int(row["pending"]),
                "last_synced_at": row["last_synced_at"],
            }

    def get_unsynced(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve unsynced records up to limit."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, query_type, raw_tokens, optimized_tokens, tokens_saved,
                    duration_ms, repo_name, branch, commit_sha, model_name, timestamp
                FROM mcp_invocations
                WHERE synced_to_cloud = 0
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_synced(self, ids: list[int], synced_at: str | None = None) -> None:
        """Mark invocation IDs as successfully synced to cloud."""
        if not ids:
            return
        ts = synced_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        placeholders = ",".join("?" for _ in ids)
        with self._get_connection() as conn:
            conn.execute(
                f"""
                UPDATE mcp_invocations
                SET synced_to_cloud = 1, synced_at = ?
                WHERE id IN ({placeholders})
                """,  # noqa: S608
                [ts, *ids],
            )

    def reset(self) -> None:
        """Delete all metrics history."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM mcp_invocations")
            conn.execute("VACUUM")
