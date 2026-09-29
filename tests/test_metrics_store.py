"""Tests for MetricsStore (.lineagelens/metrics.sqlite)."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lineagelens.store.metrics_store import (
    PRICING_MODELS,
    MetricRecord,
    MetricsStore,
)


@pytest.fixture
def store(tmp_path: Path) -> MetricsStore:
    db_file = tmp_path / ".lineagelens" / "metrics.sqlite"
    return MetricsStore(db_file)


def test_metrics_store_initialization(store: MetricsStore) -> None:
    assert store.db_path.exists()
    status = store.get_cloud_sync_status()
    assert status["total"] == 0
    assert status["synced"] == 0
    assert status["pending"] == 0
    assert status["last_synced_at"] is None


def test_record_single_invocation(store: MetricsStore) -> None:
    record = MetricRecord(
        tool_name="find_paths",
        query_type="mcp_find_paths",
        raw_tokens=14200,
        optimized_tokens=420,
        tokens_saved=13780,
        duration_ms=18,
        response_bytes=1680,
        repo_name="LineageLens",
        branch="main",
        commit_sha="abcdef123456",
        model_name="gpt-4o",
    )
    row_id = store.record(record)
    assert row_id > 0

    summary = store.get_summary(period="all", model_name="gpt-4o")
    assert summary["total_invocations"] == 1
    assert summary["raw_tokens"] == 14200
    assert summary["optimized_tokens"] == 420
    assert summary["tokens_saved"] == 13780
    assert summary["reduction_percentage"] == round((13780 / 14200) * 100, 2)
    assert summary["cost_savings_usd"] == round((13780 / 1_000_000) * 5.00, 2)
    assert summary["avg_duration_ms"] == 18.0

    by_tool = store.get_by_tool(period="all")
    assert "find_paths" in by_tool
    assert by_tool["find_paths"]["calls"] == 1
    assert by_tool["find_paths"]["tokens_saved"] == 13780


def test_concurrent_writes(store: MetricsStore) -> None:
    def write_call(i: int) -> int:
        rec = MetricRecord(
            tool_name="impact_of",
            query_type="mcp_impact_of",
            raw_tokens=2000 + i,
            optimized_tokens=200,
            tokens_saved=1800 + i,
            duration_ms=25,
            response_bytes=800,
            repo_name="LineageLens",
        )
        return store.record(rec)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write_call, range(50)))

    assert len(results) == 50
    summary = store.get_summary()
    assert summary["total_invocations"] == 50


def test_pricing_models(store: MetricsStore) -> None:
    rec = MetricRecord(
        tool_name="explain",
        query_type="mcp_explain",
        raw_tokens=1_000_000,
        optimized_tokens=0,
        tokens_saved=1_000_000,
        duration_ms=10,
        response_bytes=0,
        repo_name="LineageLens",
    )
    store.record(rec)

    for model, rate in PRICING_MODELS.items():
        summary = store.get_summary(model_name=model)
        assert summary["cost_savings_usd"] == round(rate, 2)


def test_sync_state_and_mark_synced(store: MetricsStore) -> None:
    id1 = store.record(MetricRecord(
        tool_name="callers_of",
        query_type="mcp_callers_of",
        raw_tokens=3000,
        optimized_tokens=300,
        tokens_saved=2700,
        duration_ms=15,
        response_bytes=1200,
        repo_name="LineageLens",
    ))
    id2 = store.record(MetricRecord(
        tool_name="callees_of",
        query_type="mcp_callees_of",
        raw_tokens=4000,
        optimized_tokens=400,
        tokens_saved=3600,
        duration_ms=12,
        response_bytes=1600,
        repo_name="LineageLens",
    ))

    status = store.get_cloud_sync_status()
    assert status["total"] == 2
    assert status["synced"] == 0
    assert status["pending"] == 2

    unsynced = store.get_unsynced(limit=10)
    assert len(unsynced) == 2
    assert unsynced[0]["id"] == id1
    assert unsynced[1]["id"] == id2

    store.mark_synced([id1])
    status2 = store.get_cloud_sync_status()
    assert status2["synced"] == 1
    assert status2["pending"] == 1
    assert status2["last_synced_at"] is not None

    unsynced2 = store.get_unsynced(limit=10)
    assert len(unsynced2) == 1
    assert unsynced2[0]["id"] == id2


def test_reset_store(store: MetricsStore) -> None:
    store.record(MetricRecord(
        tool_name="explore",
        query_type="mcp_explore",
        raw_tokens=5000,
        optimized_tokens=500,
        tokens_saved=4500,
        duration_ms=20,
        response_bytes=2000,
        repo_name="LineageLens",
    ))
    assert store.get_summary()["total_invocations"] == 1
    store.reset()
    assert store.get_summary()["total_invocations"] == 0


def test_metrics_persist_across_graph_rebuild(tmp_path: Path) -> None:
    from lineagelens.store import GraphStore

    project = tmp_path / "app"
    project.mkdir()
    metrics_store = MetricsStore.for_project(project)
    metrics_store.record(MetricRecord(
        tool_name="find_paths",
        query_type="mcp_find_paths",
        raw_tokens=1000,
        optimized_tokens=100,
        tokens_saved=900,
        duration_ms=15,
        response_bytes=400,
        repo_name="app",
    ))
    assert metrics_store.get_summary()["total_invocations"] == 1

    # GraphStore creation and rebuild in same project folder
    graph_db = project / ".lineagelens" / "graph.sqlite"
    with GraphStore.create(graph_db, project_root=str(project)):
        pass
    assert metrics_store.get_summary()["total_invocations"] == 1

    # Force rebuild
    with GraphStore.create(graph_db, project_root=str(project), overwrite=True):
        pass
    assert metrics_store.get_summary()["total_invocations"] == 1

