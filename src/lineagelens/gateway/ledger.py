"""
ContextServe Token Ledger & Metering Engine.
Provides double-entry token ledger accounting, virtual key management,
and hard budget ceiling enforcement.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class InsufficientBudgetError(Exception):
    """Raised when tenant balance is depleted or virtual key budget limit is exceeded."""
    def __init__(self, message: str, tenant_id: str, remaining_balance: float):
        super().__init__(message)
        self.tenant_id = tenant_id
        self.remaining_balance = remaining_balance


class KeyPermissionError(Exception):
    """Raised when a key is inactive, expired, or not authorized for a model."""


@dataclass
class Tenant:
    id: str
    name: str
    plan_tier: str
    balance_usd: float
    currency: str
    is_active: bool
    created_at: str


@dataclass
class VirtualKey:
    key_hash: str
    tenant_id: str
    key_name: str
    max_budget_usd: float | None
    spend_usd: float
    rate_limit_rpm: int
    rate_limit_tpm: int
    allowed_models: list[str] | None
    expires_at: str | None
    is_active: bool
    created_at: str
    raw_key: str | None = None  # only populated upon creation


@dataclass
class TransactionRecord:
    request_id: str
    tenant_id: str
    key_hash: str
    model_name: str
    tier: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: int
    cost_raw_usd: float
    billed_amount_usd: float
    remaining_balance: float
    status: str = "ok"
    metadata: dict[str, Any] | None = None
    transaction_id: int | None = None
    timestamp: str | None = None


class LedgerDB:
    """Manages SQLite/PostgreSQL double-entry token ledger."""

    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = Path(db_path) if isinstance(db_path, str) and db_path != ":memory:" else db_path
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)

        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        schema_file = Path(__file__).parent / "schema.sql"
        if schema_file.is_file():
            schema_sql = schema_file.read_text("utf-8")
        else:
            # Fallback inline schema
            schema_sql = """
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                plan_tier TEXT NOT NULL DEFAULT 'usage',
                balance_usd DECIMAL(12, 6) NOT NULL DEFAULT 0.000000,
                currency TEXT NOT NULL DEFAULT 'USD',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS virtual_keys (
                key_hash TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                key_name TEXT NOT NULL,
                max_budget_usd DECIMAL(10, 4),
                spend_usd DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
                rate_limit_rpm INTEGER DEFAULT 600,
                rate_limit_tpm INTEGER DEFAULT 200000,
                allowed_models TEXT,
                expires_at TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS token_ledger (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                key_hash TEXT NOT NULL REFERENCES virtual_keys(key_hash),
                request_id TEXT NOT NULL UNIQUE,
                model_name TEXT NOT NULL,
                tier TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                cost_raw_usd DECIMAL(10, 6) NOT NULL,
                billed_amount_usd DECIMAL(10, 6) NOT NULL,
                remaining_balance DECIMAL(12, 6) NOT NULL,
                status TEXT NOT NULL DEFAULT 'ok',
                metadata TEXT
            );
            """
        with self._conn:
            self._conn.executescript(schema_sql)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """SHA-256 digest of virtual API key."""
        return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------------
    # Tenant Operations
    # ------------------------------------------------------------------------

    def create_tenant(
        self,
        tenant_id: str,
        name: str,
        plan_tier: str = "growth",
        initial_balance_usd: float = 0.0,
        currency: str = "USD",
    ) -> Tenant:
        now_str = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO tenants (id, name, plan_tier, balance_usd, currency, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    plan_tier = excluded.plan_tier,
                    balance_usd = excluded.balance_usd
                """,
                (tenant_id, name, plan_tier, initial_balance_usd, currency, now_str),
            )
        return self.get_tenant(tenant_id)  # type: ignore

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        cur = self._conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,))
        row = cur.fetchone()
        if not row:
            return None
        return Tenant(
            id=row["id"],
            name=row["name"],
            plan_tier=row["plan_tier"],
            balance_usd=float(row["balance_usd"]),
            currency=row["currency"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def deposit_funds(self, tenant_id: str, amount_usd: float) -> float:
        """Add prepaid balance to a tenant account."""
        with self._conn:
            self._conn.execute(
                "UPDATE tenants SET balance_usd = balance_usd + ? WHERE id = ?",
                (amount_usd, tenant_id),
            )
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")
        return tenant.balance_usd

    # ------------------------------------------------------------------------
    # Virtual Key Operations
    # ------------------------------------------------------------------------

    def create_virtual_key(
        self,
        tenant_id: str,
        key_name: str,
        max_budget_usd: float | None = None,
        allowed_models: list[str] | None = None,
        rate_limit_rpm: int = 600,
        rate_limit_tpm: int = 200000,
        expires_at: str | None = None,
    ) -> VirtualKey:
        # Generate raw key with standard sk-cs- prefix
        raw_key = f"sk-cs-{secrets.token_urlsafe(32)}"
        key_hash = self.hash_key(raw_key)
        models_json = json.dumps(allowed_models) if allowed_models else None
        now_str = datetime.now(timezone.utc).isoformat()

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO virtual_keys (
                    key_hash, tenant_id, key_name, max_budget_usd, spend_usd,
                    rate_limit_rpm, rate_limit_tpm, allowed_models, expires_at, is_active, created_at
                ) VALUES (?, ?, ?, ?, 0.0, ?, ?, ?, ?, 1, ?)
                """,
                (
                    key_hash,
                    tenant_id,
                    key_name,
                    max_budget_usd,
                    rate_limit_rpm,
                    rate_limit_tpm,
                    models_json,
                    expires_at,
                    now_str,
                ),
            )

        vk = self.get_virtual_key_by_hash(key_hash)
        if vk:
            vk.raw_key = raw_key
            return vk
        raise RuntimeError("Failed to create virtual key")

    def get_virtual_key_by_hash(self, key_hash: str) -> VirtualKey | None:
        cur = self._conn.execute("SELECT * FROM virtual_keys WHERE key_hash = ?", (key_hash,))
        row = cur.fetchone()
        if not row:
            return None
        models = json.loads(row["allowed_models"]) if row["allowed_models"] else None
        return VirtualKey(
            key_hash=row["key_hash"],
            tenant_id=row["tenant_id"],
            key_name=row["key_name"],
            max_budget_usd=float(row["max_budget_usd"]) if row["max_budget_usd"] is not None else None,
            spend_usd=float(row["spend_usd"]),
            rate_limit_rpm=int(row["rate_limit_rpm"]),
            rate_limit_tpm=int(row["rate_limit_tpm"]),
            allowed_models=models,
            expires_at=row["expires_at"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
        )

    def list_virtual_keys(self, tenant_id: str | None = None) -> list[VirtualKey]:
        if tenant_id:
            cur = self._conn.execute("SELECT * FROM virtual_keys WHERE tenant_id = ?", (tenant_id,))
        else:
            cur = self._conn.execute("SELECT * FROM virtual_keys ORDER BY created_at DESC")
        keys = []
        for row in cur.fetchall():
            models = json.loads(row["allowed_models"]) if row["allowed_models"] else None
            keys.append(
                VirtualKey(
                    key_hash=row["key_hash"],
                    tenant_id=row["tenant_id"],
                    key_name=row["key_name"],
                    max_budget_usd=float(row["max_budget_usd"]) if row["max_budget_usd"] is not None else None,
                    spend_usd=float(row["spend_usd"]),
                    rate_limit_rpm=int(row["rate_limit_rpm"]),
                    rate_limit_tpm=int(row["rate_limit_tpm"]),
                    allowed_models=models,
                    expires_at=row["expires_at"],
                    is_active=bool(row["is_active"]),
                    created_at=row["created_at"],
                )
            )
        return keys

    # ------------------------------------------------------------------------
    # Quota Verification & Metered Transactions
    # ------------------------------------------------------------------------

    def verify_and_reserve(
        self,
        raw_key_or_hash: str,
        model_name: str,
        estimated_cost_usd: float = 0.00001,
    ) -> tuple[VirtualKey, Tenant]:
        """Verify API key validity, expiration, model permissions, and remaining budget."""
        key_hash = raw_key_or_hash if len(raw_key_or_hash) == 64 else self.hash_key(raw_key_or_hash)
        vk = self.get_virtual_key_by_hash(key_hash)
        if not vk or not vk.is_active:
            raise KeyPermissionError("Virtual key is invalid or inactive")

        if vk.expires_at:
            exp = datetime.fromisoformat(vk.expires_at.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                raise KeyPermissionError("Virtual key has expired")

        if vk.allowed_models and model_name not in vk.allowed_models:
            raise KeyPermissionError(f"Model '{model_name}' is not permitted for this key")

        # Check virtual key budget ceiling
        if vk.max_budget_usd is not None and (vk.spend_usd + estimated_cost_usd) > vk.max_budget_usd:
            raise InsufficientBudgetError(
                f"Virtual key budget cap of ${vk.max_budget_usd:.2f} reached",
                tenant_id=vk.tenant_id,
                remaining_balance=max(0.0, vk.max_budget_usd - vk.spend_usd),
            )

        # Check tenant account balance
        tenant = self.get_tenant(vk.tenant_id)
        if not tenant or not tenant.is_active:
            raise KeyPermissionError(f"Tenant '{vk.tenant_id}' is inactive")

        if tenant.balance_usd < estimated_cost_usd:
            raise InsufficientBudgetError(
                f"Tenant balance depleted (${tenant.balance_usd:.4f} remaining)",
                tenant_id=tenant.id,
                remaining_balance=tenant.balance_usd,
            )

        return vk, tenant

    def record_transaction(self, tx: TransactionRecord) -> TransactionRecord:
        """Atomically deduct balance, update key spend, and log transaction."""
        meta_json = json.dumps(tx.metadata) if tx.metadata else None
        now_str = datetime.now(timezone.utc).isoformat()

        with self._conn:
            # 1. Deduct balance from tenant
            self._conn.execute(
                "UPDATE tenants SET balance_usd = balance_usd - ? WHERE id = ?",
                (tx.billed_amount_usd, tx.tenant_id),
            )

            # 2. Update spend on virtual key
            self._conn.execute(
                "UPDATE virtual_keys SET spend_usd = spend_usd + ? WHERE key_hash = ?",
                (tx.billed_amount_usd, tx.key_hash),
            )

            # 3. Retrieve new balance
            cur = self._conn.execute("SELECT balance_usd FROM tenants WHERE id = ?", (tx.tenant_id,))
            row = cur.fetchone()
            rem_balance = float(row["balance_usd"]) if row else 0.0

            # 4. Insert ledger entry
            cur = self._conn.execute(
                """
                INSERT INTO token_ledger (
                    timestamp, tenant_id, key_hash, request_id, model_name, tier,
                    prompt_tokens, completion_tokens, total_tokens, duration_ms,
                    cost_raw_usd, billed_amount_usd, remaining_balance, status, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_str,
                    tx.tenant_id,
                    tx.key_hash,
                    tx.request_id,
                    tx.model_name,
                    tx.tier,
                    tx.prompt_tokens,
                    tx.completion_tokens,
                    tx.total_tokens,
                    tx.duration_ms,
                    tx.cost_raw_usd,
                    tx.billed_amount_usd,
                    rem_balance,
                    tx.status,
                    meta_json,
                ),
            )
            tx.transaction_id = cur.lastrowid
            tx.timestamp = now_str
            tx.remaining_balance = rem_balance

        return tx

    def get_ledger_summary(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Aggregates System 1 vs System 2 tokens, requests, and costs."""
        if tenant_id:
            query = """
            SELECT
                tier,
                COUNT(*) as request_count,
                SUM(prompt_tokens) as total_prompt_tokens,
                SUM(completion_tokens) as total_completion_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(billed_amount_usd) as total_billed_usd,
                AVG(duration_ms) as avg_duration_ms
            FROM token_ledger
            WHERE tenant_id = ?
            GROUP BY tier
            """
            cur = self._conn.execute(query, (tenant_id,))
        else:
            query = """
            SELECT
                tier,
                COUNT(*) as request_count,
                SUM(prompt_tokens) as total_prompt_tokens,
                SUM(completion_tokens) as total_completion_tokens,
                SUM(total_tokens) as total_tokens,
                SUM(billed_amount_usd) as total_billed_usd,
                AVG(duration_ms) as avg_duration_ms
            FROM token_ledger
            GROUP BY tier
            """
            cur = self._conn.execute(query)

        tiers: dict[str, dict[str, Any]] = {}
        total_requests = 0
        total_billed = 0.0
        total_tokens = 0

        for row in cur.fetchall():
            t_name = row["tier"]
            req_c = row["request_count"] or 0
            b_usd = float(row["total_billed_usd"] or 0.0)
            toks = row["total_tokens"] or 0
            tiers[t_name] = {
                "requests": req_c,
                "prompt_tokens": row["total_prompt_tokens"] or 0,
                "completion_tokens": row["total_completion_tokens"] or 0,
                "total_tokens": toks,
                "billed_usd": round(b_usd, 6),
                "avg_duration_ms": round(float(row["avg_duration_ms"] or 0.0), 2),
            }
            total_requests += req_c
            total_billed += b_usd
            total_tokens += toks

        # Calculate System 1 vs System 2 ratio & token savings
        s1_requests = tiers.get("system1", {}).get("requests", 0)
        s1_ratio = (s1_requests / total_requests) if total_requests > 0 else 0.0

        # Estimated counterfactual cost if System 1 calls had gone to System 2 (e.g. GPT-4o @ $5/1M avg)
        s1_tokens = tiers.get("system1", {}).get("total_tokens", 0)
        s1_actual_cost = tiers.get("system1", {}).get("billed_usd", 0.0)
        s2_counterfactual_cost = (s1_tokens / 1_000_000.0) * 5.00
        cost_saved_usd = max(0.0, s2_counterfactual_cost - s1_actual_cost)

        return {
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "total_billed_usd": round(total_billed, 6),
            "system1_ratio_pct": round(s1_ratio * 100.0, 1),
            "estimated_savings_usd": round(cost_saved_usd, 4),
            "tiers": tiers,
        }

    def close(self) -> None:
        self._conn.close()
