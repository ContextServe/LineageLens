"""MCP Token Savings and ROI Reporter.

Generates visual terminal tables or machine-readable JSON summarizing local
MCP tool execution metrics, counterfactual token savings, and cloud synchronization.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .store.metrics_store import PRICING_MODELS, MetricsStore
from .sync import get_auth_token_and_base_url, sync_metrics


def _get_git_info(cwd: Path) -> tuple[str | None, str | None]:
    branch, commit_sha = None, None
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(cwd),
            text=True,
        ).strip()
        commit_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=str(cwd),
            text=True,
        ).strip()
    except Exception:
        pass
    return branch, commit_sha


def generate_report_data(
    project_root: Path,
    period: str = "all",
    model: str = "gpt-4o",
) -> dict[str, Any]:
    """Compile structured metrics report data."""
    store = MetricsStore.for_project(project_root)
    branch, _ = _get_git_info(project_root)
    sync_status = store.get_cloud_sync_status()
    summary = store.get_summary(period=period, model_name=model)
    by_tool = store.get_by_tool(period=period)

    return {
        "repo_name": project_root.resolve().name,
        "branch": branch or "unknown",
        "period": period,
        "cloud_sync": sync_status,
        "summary": summary,
        "by_tool": by_tool,
    }


def format_terminal_report(
    data: dict[str, Any],
    by_tool: bool = False,
    endpoint: str | None = None,
) -> str:
    """Format metrics report as a clean, high-impact terminal view."""
    repo = data["repo_name"]
    branch = data.get("branch", "unknown")
    period = data.get("period", "all")
    model = data["summary"].get("model_name", "gpt-4o")
    rate = PRICING_MODELS.get(model, PRICING_MODELS["gpt-4o"])

    period_labels = {
        "all": "All Time",
        "today": "Today",
        "7d": "Last 7 Days",
        "30d": "Last 30 Days",
    }
    period_str = period_labels.get(period, period)

    sync_info = data["cloud_sync"]
    total_recs = sync_info["total"]
    synced_recs = sync_info["synced"]
    pending_recs = sync_info["pending"]

    _, _, org = get_auth_token_and_base_url(endpoint)
    org_label = f" (org: {org})" if org else ""

    if pending_recs == 0 and total_recs > 0:
        sync_line = f"{synced_recs} / {total_recs} synced to ContextServe.ai{org_label}"
    elif total_recs == 0:
        sync_line = "0 / 0 synced to ContextServe.ai"
    else:
        sync_line = f"{synced_recs} / {total_recs} synced to ContextServe.ai ({pending_recs} pending, run with --sync){org_label}"

    s = data["summary"]
    total_calls = s["total_invocations"]
    raw_tokens = s["raw_tokens"]
    opt_tokens = s["optimized_tokens"]
    saved_tokens = s["tokens_saved"]
    reduction = s["reduction_percentage"]
    savings_usd = s["cost_savings_usd"]
    avg_duration = s["avg_duration_ms"]

    lines = [
        "=" * 88,
        "                      LINEAGELENS MCP TOKEN SAVINGS REPORT",
        f" Repository: {repo} (branch: {branch})",
        f" Period    : {period_str}",
        f" Target    : {model} (${rate:.2f} / 1M blended tokens)",
        f" Cloud Sync: {sync_line}",
        "=" * 88,
        "",
        "  TOTAL INVOCATIONS        BASELINE TOKENS        OPTIMIZED TOKENS       NET TOKENS SAVED",
        f"        {total_calls:<17}  {raw_tokens:>15,}        {opt_tokens:>15,}      {saved_tokens:>15,}",
        f"                             (Unindexed Context)    (LineageLens MCP)         ({reduction:.1f}% Reduction)",
        "",
        f"  ESTIMATED COST SAVINGS: ${savings_usd:.2f} USD",
    ]

    if by_tool and data["by_tool"]:
        lines.extend([
            "",
            "TOOL BREAKDOWN:",
            f"{'Tool':<20}  {'Invocations':>11}   {'Raw Tokens':>11}   {'Optimized':>9}   {'Tokens Saved':>12}   {'Reduction':>9}   {'Avg Latency':>11}",
            "-" * 96,
        ])
        for tool_name, t in data["by_tool"].items():
            lines.append(
                f"{tool_name:<20}  {t['calls']:>11}   {t['raw_tokens']:>11,}   {t['optimized_tokens']:>9,}   "
                f"{t['tokens_saved']:>12,}   {t['reduction_percentage']:>8.1f}%   {int(t['avg_duration_ms']):>9}ms"
            )
        lines.extend([
            "-" * 96,
            f"{'TOTAL':<20}  {total_calls:>11}   {raw_tokens:>11,}   {opt_tokens:>9,}   "
            f"{saved_tokens:>12,}   {reduction:>8.1f}%   {int(avg_duration):>9}ms",
        ])

    lines.append("=" * 88)
    return "\n".join(lines)


def run_report(
    project_root: Path,
    period: str = "all",
    by_tool: bool = False,
    sync: bool = False,
    endpoint: str | None = None,
    json_output: bool = False,
    model: str = "gpt-4o",
    reset: bool = False,
    yes: bool = False,
) -> int:
    """Execute report subcommand logic."""
    project_root = project_root.resolve()
    store = MetricsStore.for_project(project_root)

    if reset:
        if not yes:
            answer = input("Are you sure you want to reset all local metrics? [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("Reset cancelled.")
                return 0
        store.reset()
        print("Local metrics history reset successfully.")
        return 0

    if sync:
        print("Connecting to ContextServe.ai...")
        status = store.get_cloud_sync_status()
        pending = status["pending"]
        if pending == 0:
            print("No unsynced metrics found. Everything up to date.")
        else:
            print(f"Found {pending} unsynced invocation metrics.")
            ok, msg, _ = sync_metrics(project_root, endpoint=endpoint)
            print(msg)
            if not ok:
                return 1

    data = generate_report_data(project_root, period=period, model=model)

    if json_output:
        print(json.dumps(data, indent=2))
    else:
        print(format_terminal_report(data, by_tool=by_tool, endpoint=endpoint))

    return 0
