"""Tests for metrics synchronization to ContextServe.ai (lineagelens.sync)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lineagelens.store.metrics_store import MetricRecord, MetricsStore
from lineagelens.sync import sync_metrics


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "my_project"
    proj.mkdir()
    store = MetricsStore.for_project(proj)
    store.record(MetricRecord(
        tool_name="find_paths",
        query_type="mcp_find_paths",
        raw_tokens=10000,
        optimized_tokens=500,
        tokens_saved=9500,
        duration_ms=22,
        response_bytes=2000,
        repo_name="my_project",
        branch="main",
        commit_sha="c0ffee",
    ))
    return proj


def test_sync_unauthenticated(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTEXTSERVE_API_KEY", raising=False)
    monkeypatch.delenv("LINEAGELENS_API_TOKEN", raising=False)

    with patch("lineagelens.credentials.CredentialsStore.get", return_value={}):
        ok, msg, count = sync_metrics(project)

    assert not ok
    assert "Not authenticated to ContextServe.ai" in msg
    assert count == 0


def test_sync_batch_success(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXTSERVE_API_KEY", "ll_live_test_key_123")

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.Client.post", return_value=mock_response) as mock_post:
        ok, msg, count = sync_metrics(project, endpoint="https://test.contextserve.ai")

    assert ok
    assert count == 1
    assert "Successfully reported 1 metrics" in msg

    # Verify headers and payload
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://test.contextserve.ai/api/v1/telemetry/tokens/batch"
    assert kwargs["headers"]["X-API-Key"] == "ll_live_test_key_123"
    assert len(kwargs["json"]["events"]) == 1

    # Verify SQLite row was marked synced
    store = MetricsStore.for_project(project)
    status = store.get_cloud_sync_status()
    assert status["synced"] == 1
    assert status["pending"] == 0


def test_sync_fallback_to_single_endpoint(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTEXTSERVE_API_KEY", "jwt_token_456")

    res_404 = MagicMock(status_code=404)
    res_200 = MagicMock(status_code=200)

    # First call (/batch) returns 404, fallback call (/tokens) returns 200
    with patch("httpx.Client.post", side_effect=[res_404, res_200]) as mock_post:
        ok, _, count = sync_metrics(project, endpoint="https://test.contextserve.ai")

    assert ok
    assert count == 1
    assert mock_post.call_count == 2
    second_call_url = mock_post.call_args_list[1][0][0]
    assert second_call_url == "https://test.contextserve.ai/api/v1/telemetry/tokens"

    store = MetricsStore.for_project(project)
    status = store.get_cloud_sync_status()
    assert status["synced"] == 1
