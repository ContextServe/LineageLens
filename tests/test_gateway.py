"""
Tests for ContextServe Hybrid Token Gateway & Ledger (src/lineagelens/gateway/).
"""
import pytest

from lineagelens.gateway.client import (
    GatewayClient,
    _local_heuristic_decision,
    _local_heuristic_score,
)
from lineagelens.gateway.ledger import (
    InsufficientBudgetError,
    KeyPermissionError,
    LedgerDB,
    TransactionRecord,
)


class TestLedgerAndVirtualKeys:
    @pytest.fixture
    def ledger(self, tmp_path):
        db_file = tmp_path / "test_ledger.sqlite"
        db = LedgerDB(db_file)
        yield db
        db.close()

    def test_tenant_creation_and_deposit(self, ledger):
        tenant = ledger.create_tenant(
            tenant_id="org_test_001",
            name="Test Engineering",
            plan_tier="growth",
            initial_balance_usd=25.0,
        )
        assert tenant.id == "org_test_001"
        assert tenant.balance_usd == 25.0
        assert tenant.is_active is True

        new_bal = ledger.deposit_funds("org_test_001", 15.0)
        assert new_bal == 40.0

        retrieved = ledger.get_tenant("org_test_001")
        assert retrieved.balance_usd == 40.0

    def test_virtual_key_creation_and_hashing(self, ledger):
        ledger.create_tenant("org_test_002", name="Org 2", initial_balance_usd=50.0)
        vk = ledger.create_virtual_key(
            tenant_id="org_test_002",
            key_name="LineageLens-MCP-Prod",
            max_budget_usd=10.0,
            allowed_models=["laya-decision", "gpt-4o"],
        )
        assert vk.raw_key.startswith("sk-cs-")
        assert len(vk.key_hash) == 64
        assert vk.spend_usd == 0.0
        assert vk.allowed_models == ["laya-decision", "gpt-4o"]

        # Verify key resolution by hash
        retrieved = ledger.get_virtual_key_by_hash(vk.key_hash)
        assert retrieved is not None
        assert retrieved.key_name == "LineageLens-MCP-Prod"

        # Verify with verify_and_reserve
        vk_res, tenant_res = ledger.verify_and_reserve(vk.raw_key, model_name="laya-decision")
        assert vk_res.key_hash == vk.key_hash
        assert tenant_res.id == "org_test_002"

    def test_model_permission_enforcement(self, ledger):
        ledger.create_tenant("org_test_003", name="Org 3", initial_balance_usd=50.0)
        vk = ledger.create_virtual_key(
            tenant_id="org_test_003",
            key_name="Restricted-Key",
            allowed_models=["laya-decision"],
        )
        # Permitted model
        ledger.verify_and_reserve(vk.raw_key, model_name="laya-decision")

        # Disallowed model
        with pytest.raises(KeyPermissionError, match="not permitted"):
            ledger.verify_and_reserve(vk.raw_key, model_name="claude-3-5-sonnet")

    def test_budget_cap_and_balance_deduction(self, ledger):
        ledger.create_tenant("org_test_004", name="Org 4", initial_balance_usd=1.00)
        vk = ledger.create_virtual_key(
            tenant_id="org_test_004",
            key_name="Small-Budget",
            max_budget_usd=0.50,
        )

        # 1. Record System 1 transaction ($0.10)
        tx1 = TransactionRecord(
            request_id="req-001",
            tenant_id="org_test_004",
            key_hash=vk.key_hash,
            model_name="laya-decision",
            tier="system1",
            prompt_tokens=100,
            completion_tokens=2,
            total_tokens=102,
            duration_ms=12,
            cost_raw_usd=0.000005,
            billed_amount_usd=0.10,
            remaining_balance=0.90,
        )
        ledger.record_transaction(tx1)

        # Verify tenant balance deducted
        tenant = ledger.get_tenant("org_test_004")
        assert round(tenant.balance_usd, 2) == 0.90

        # Verify key spend updated
        vk_updated = ledger.get_virtual_key_by_hash(vk.key_hash)
        assert round(vk_updated.spend_usd, 2) == 0.10

        # 2. Record large transaction ($0.45) -> total spend $0.55 > max_budget $0.50
        tx2 = TransactionRecord(
            request_id="req-002",
            tenant_id="org_test_004",
            key_hash=vk.key_hash,
            model_name="gpt-4o",
            tier="system2",
            prompt_tokens=4000,
            completion_tokens=800,
            total_tokens=4800,
            duration_ms=850,
            cost_raw_usd=0.03,
            billed_amount_usd=0.45,
            remaining_balance=0.45,
        )
        ledger.record_transaction(tx2)

        # 3. Next request should fail with InsufficientBudgetError due to key cap
        with pytest.raises(InsufficientBudgetError, match="budget cap"):
            ledger.verify_and_reserve(vk.raw_key, model_name="laya-decision")

    def test_ledger_summary_and_savings_calculation(self, ledger):
        ledger.create_tenant("org_summary", name="Summary Org", initial_balance_usd=100.0)
        vk = ledger.create_virtual_key(tenant_id="org_summary", key_name="Key-Summary")

        # 10 System 1 transactions
        for i in range(10):
            ledger.record_transaction(
                TransactionRecord(
                    request_id=f"s1-{i}",
                    tenant_id="org_summary",
                    key_hash=vk.key_hash,
                    model_name="laya-decision",
                    tier="system1",
                    prompt_tokens=500,
                    completion_tokens=2,
                    total_tokens=502,
                    duration_ms=14,
                    cost_raw_usd=0.000025,
                    billed_amount_usd=0.00005,
                    remaining_balance=100.0,
                )
            )

        # 2 System 2 transactions
        for i in range(2):
            ledger.record_transaction(
                TransactionRecord(
                    request_id=f"s2-{i}",
                    tenant_id="org_summary",
                    key_hash=vk.key_hash,
                    model_name="claude-3-5-sonnet",
                    tier="system2",
                    prompt_tokens=3000,
                    completion_tokens=500,
                    total_tokens=3500,
                    duration_ms=1100,
                    cost_raw_usd=0.018,
                    billed_amount_usd=0.025,
                    remaining_balance=99.95,
                )
            )

        summary = ledger.get_ledger_summary(tenant_id="org_summary")
        assert summary["total_requests"] == 12
        assert summary["system1_ratio_pct"] > 80.0
        assert "system1" in summary["tiers"]
        assert "system2" in summary["tiers"]
        assert summary["tiers"]["system1"]["requests"] == 10
        assert summary["tiers"]["system2"]["requests"] == 2
        assert summary["estimated_savings_usd"] >= 0.0


class TestGatewayClientAndFallbacks:
    def test_local_heuristic_decision(self):
        assert _local_heuristic_decision("Is this order service relevant?") == "true"
        assert _local_heuristic_decision("HTTP 401 Unauthorized token expired") == "authentication_error"
        assert _local_heuristic_decision("Connection timed out 504") == "network_timeout"
        assert _local_heuristic_decision("TypeError: NoneType object has no attribute") == "code_defect"

    def test_local_heuristic_score(self):
        score_high = _local_heuristic_score("query target match contract impact")
        assert score_high >= 0.60
        score_low = _local_heuristic_score("irrelevant random noise skip")
        assert score_low <= 0.50

    def test_client_offline_fallback(self):
        # Client pointing to non-existent port should gracefully return fallback decision
        client = GatewayClient(
            gateway_url="http://127.0.0.1:59999",
            enable_local_fallback=True,
            timeout_seconds=0.5,
        )
        res = client.fast_decision("Is auth service handler relevant?")
        assert res in ("true", "false")

        score = client.relevance_score("target order match")
        assert 0.0 <= score <= 1.0


class TestGatewayCLI:
    def test_cli_gateway_status(self, capsys):
        from lineagelens.cli import main

        code = main(["gateway", "status", "--url", "http://127.0.0.1:59999"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "ContextServe Hybrid AI Token Gateway" in captured

    def test_cli_gateway_test(self, capsys):
        from lineagelens.cli import main

        code = main(["gateway", "test", "--prompt", "Is payment handler relevant?"])
        assert code == 0
        captured = capsys.readouterr().out
        assert "Decision" in captured
        assert "Relevance" in captured

    def test_cli_gateway_keys_and_report(self, tmp_path, capsys):
        from lineagelens.cli import main

        db_path = str(tmp_path / "cli_ledger.sqlite")

        # Create key
        code = main(["gateway", "keys", "--create", "TestKey", "--tenant", "org_cli", "--budget", "15.0", "--db", db_path])
        assert code == 0
        captured = capsys.readouterr().out
        assert "Created virtual API key" in captured
        assert "sk-cs-" in captured

        # List keys
        code = main(["gateway", "keys", "--list", "--db", db_path])
        assert code == 0
        captured = capsys.readouterr().out
        assert "TestKey" in captured

        # Report
        code = main(["gateway", "report", "--db", db_path])
        assert code == 0
        captured = capsys.readouterr().out
        assert "ContextServe Hybrid Token Gateway Usage & Cost Ledger" in captured
