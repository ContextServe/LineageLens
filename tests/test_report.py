"""Tests for report generation and CLI report subcommand."""

import json
from pathlib import Path

import pytest

from lineagelens.cli import main
from lineagelens.store.metrics_store import MetricRecord, MetricsStore


@pytest.fixture
def project_with_metrics(tmp_path: Path) -> Path:
    proj = tmp_path / "repo"
    proj.mkdir()
    store = MetricsStore.for_project(proj)
    store.record(MetricRecord(
        tool_name="find_paths",
        query_type="mcp_find_paths",
        raw_tokens=542000,
        optimized_tokens=12400,
        tokens_saved=529600,
        duration_ms=19,
        response_bytes=49600,
        repo_name="repo",
        branch="main",
    ))
    store.record(MetricRecord(
        tool_name="impact_of",
        query_type="mcp_impact_of",
        raw_tokens=480000,
        optimized_tokens=9800,
        tokens_saved=470200,
        duration_ms=31,
        response_bytes=39200,
        repo_name="repo",
        branch="main",
    ))
    return proj


def test_cli_report_json(project_with_metrics: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["report", str(project_with_metrics), "--json"])
    assert code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["repo_name"] == "repo"
    assert data["summary"]["total_invocations"] == 2
    assert data["summary"]["raw_tokens"] == 1022000
    assert data["summary"]["optimized_tokens"] == 22200
    assert data["summary"]["tokens_saved"] == 999800
    assert "find_paths" in data["by_tool"]
    assert "impact_of" in data["by_tool"]


def test_cli_report_terminal_table(
    project_with_metrics: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["report", str(project_with_metrics), "--by-tool"])
    assert code == 0

    captured = capsys.readouterr()
    assert "LINEAGELENS MCP TOKEN SAVINGS REPORT" in captured.out
    assert "TOTAL INVOCATIONS" in captured.out
    assert "TOOL BREAKDOWN" in captured.out
    assert "find_paths" in captured.out
    assert "impact_of" in captured.out
    assert "ESTIMATED COST SAVINGS" in captured.out


def test_cli_report_reset_prompt_cancel(
    project_with_metrics: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "n")
    code = main(["report", str(project_with_metrics), "--reset"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Reset cancelled." in captured.out

    store = MetricsStore.for_project(project_with_metrics)
    assert store.get_summary()["total_invocations"] == 2


def test_cli_report_reset_with_yes(
    project_with_metrics: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["report", str(project_with_metrics), "--reset", "-y"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Local metrics history reset successfully." in captured.out

    store = MetricsStore.for_project(project_with_metrics)
    assert store.get_summary()["total_invocations"] == 0
