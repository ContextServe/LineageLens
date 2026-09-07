"""Generalized reporting for multi-tool benchmark suites.

Produces markdown and JSON reports for both PR-replication (Suite A)
and architecture-Q&A (Suite B) suites.
"""

import json
import statistics
from pathlib import Path

from arms import Arm
from common import compute_metrics, log


def write_pr_report(
    results: list[dict],
    arms: list[Arm],
    ground_truth: set,
    work_dir: str,
    pr_num: int,
    versions: dict = None,
) -> tuple[Path, Path]:
    """Generate summary.md and summary.json for PR-replication suite (N-arm variant).

    Args:
        results: List of result dicts from run_claude_scenario()
        arms: List of Arm objects that were benchmarked
        ground_truth: Set of files changed in the PR (ground truth)
        work_dir: Work directory for output
        pr_num: PR number for naming
        versions: Dict of tool versions (optional)

    Returns:
        (summary_md_path, summary_json_path)
    """
    work_path = Path(work_dir)
    results_dir = work_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Build results lookup by arm_name
    results_by_arm = {r["arm_name"]: r for r in results}

    # Compute metrics per arm
    metrics_by_arm = {}
    for arm in arms:
        result = results_by_arm.get(arm.name, {})
        metrics = compute_metrics(result.get("files_identified", []), ground_truth)
        metrics_by_arm[arm.name] = metrics

    # Write summary.md (N-column table)
    summary_md_path = results_dir / "summary.md"
    with open(summary_md_path, "w") as f:
        f.write(f"# Benchmark Results: PR #{pr_num}\n\n")
        f.write("## Overview\nComparison of code-graph tools for PR file-list prediction.\n\n")

        # Metrics table (one column per arm)
        arm_names = [arm.name for arm in arms]
        f.write("| Metric | " + " | ".join(arm_names) + " |\n")
        f.write("|--------|" + "|".join(["---" for _ in arm_names]) + "|\n")

        metrics_to_report = ["cost_usd", "tokens_in", "tokens_out", "duration_sec"]
        for metric in metrics_to_report:
            f.write(f"| {metric} ")
            for arm_name in arm_names:
                result = results_by_arm.get(arm_name, {})
                value = result.get(metric, 0)
                f.write(f"| {value:.2f} " if isinstance(value, float) else f"| {value} ")
            f.write("|\n")

        # F1 table
        f.write("\n| Metric | " + " | ".join(arm_names) + " |\n")
        f.write("|--------|" + "|".join(["---" for _ in arm_names]) + "|\n")
        f.write(
            "| Precision | " + " | ".join([f"{metrics_by_arm[n]['precision']:.3f}" for n in arm_names]) + " |\n"
        )
        f.write("| Recall | " + " | ".join([f"{metrics_by_arm[n]['recall']:.3f}" for n in arm_names]) + " |\n")
        f.write("| F1 Score | " + " | ".join([f"{metrics_by_arm[n]['f1']:.3f}" for n in arm_names]) + " |\n")

        # Ground truth
        f.write("\n## Ground Truth\n\n")
        f.write(f"Files changed in PR: {len(ground_truth)}\n\n")
        for fname in sorted(ground_truth):
            f.write(f"- {fname}\n")

        # Per-arm details
        for arm in arms:
            result = results_by_arm.get(arm.name, {})
            metrics = metrics_by_arm[arm.name]

            f.write(f"\n## Arm: {arm.name}\n\n")
            f.write(f"- Cost: ${result.get('cost_usd', 0):.2f}\n")
            f.write(f"- Duration: {result.get('duration_sec', 0):.1f}s\n")
            f.write(f"- Tool calls: {len(result.get('tool_calls', []))}\n")
            f.write(f"- Files identified: {metrics['identified_count']}\n")
            f.write(
                f"- Precision: {metrics['precision']:.3f}, Recall: {metrics['recall']:.3f}, F1: {metrics['f1']:.3f}\n"
            )

            if result.get("source_files_read"):
                f.write(f"⚠️  Source file reads detected: {len(result['source_files_read'])}\n")

        # Versions (if captured)
        if versions:
            f.write("\n## Tool Versions\n\n")
            for tool, version in versions.items():
                f.write(f"- {tool}: {version}\n")

    # Write summary.json
    summary_json_path = results_dir / "summary.json"
    summary_data = {
        "pr_number": pr_num,
        "ground_truth_files": sorted(ground_truth),
        "versions": versions or {},
        "arms": {},
    }

    for arm in arms:
        result = results_by_arm.get(arm.name, {})
        metrics = metrics_by_arm[arm.name]

        summary_data["arms"][arm.name] = {
            "cost_usd": result.get("cost_usd", 0),
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "duration_sec": result.get("duration_sec", 0),
            "tool_calls": len(result.get("tool_calls", [])),
            "metrics": metrics,
            "files_identified": result.get("files_identified", []),
        }

    with open(summary_json_path, "w") as f:
        json.dump(summary_data, f, indent=2)

    log(f"Report written to {summary_md_path}", "INFO")
    return (summary_md_path, summary_json_path)


def write_arch_report(
    aggregated: dict[str, dict],
    arms: list[Arm],
    work_dir: str,
    versions: dict = None,
) -> tuple[Path, Path]:
    """Generate summary.md and summary.json for architecture-Q&A suite.

    Tables are formatted: repos × metrics, columns = arms, cells = medians.

    Args:
        aggregated: Dict mapping repo_url -> aggregated results
        arms: List of Arm objects
        work_dir: Work directory for output
        versions: Dict of tool versions (optional)

    Returns:
        (summary_md_path, summary_json_path)
    """
    work_path = Path(work_dir)
    results_dir = work_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    summary_md_path = results_dir / "summary.md"
    with open(summary_md_path, "w") as f:
        f.write("# Architecture-Q&A Benchmark Results\n\n")
        f.write("Median values across multiple runs per arm and repo.\n\n")

        arm_names = [arm.name for arm in arms]

        # Per-metric table
        metrics = ["median_tool_calls", "median_duration_sec", "median_tokens", "median_cost_usd"]

        for metric in metrics:
            f.write(f"\n## {metric}\n\n")
            f.write("| Repo | " + " | ".join(arm_names) + " |\n")
            f.write("|------|" + "|".join(["---" for _ in arm_names]) + "|\n")

            for repo_url, repo_data in aggregated.items():
                repo_name = repo_url.split("/")[-1]
                f.write(f"| {repo_name} ")

                for arm_name in arm_names:
                    arm_data = repo_data.get("arms", {}).get(arm_name, {})
                    value = arm_data.get(metric, "N/A")

                    f.write(f"| {value:.2f} " if isinstance(value, float) else f"| {value} ")

                f.write("|\n")

            # Reduction percentages vs baseline
            f.write("\n### % Reduction vs Baseline\n\n")
            f.write("| Repo | " + " | ".join([a for a in arm_names if a != "baseline"]) + " |\n")
            f.write("|------|" + "|".join(["---" for _ in arm_names if _ != "baseline"]) + "|\n")

            for repo_url, repo_data in aggregated.items():
                repo_name = repo_url.split("/")[-1]
                baseline_val = repo_data.get("arms", {}).get("baseline", {}).get(metric)
                if baseline_val is None or baseline_val == 0:
                    continue

                f.write(f"| {repo_name} ")

                for arm_name in arm_names:
                    if arm_name == "baseline":
                        continue

                    arm_val = repo_data.get("arms", {}).get(arm_name, {}).get(metric)
                    if arm_val is not None and baseline_val != 0:
                        reduction = (baseline_val - arm_val) / baseline_val * 100
                        f.write(f"| {reduction:.1f}% ")
                    else:
                        f.write("| N/A ")

                f.write("|\n")

        # Versions
        if versions:
            f.write("\n## Tool Versions\n\n")
            for tool, version in versions.items():
                f.write(f"- {tool}: {version}\n")

    # Write summary.json
    summary_json_path = results_dir / "summary.json"
    with open(summary_json_path, "w") as f:
        json.dump({"aggregated": aggregated, "versions": versions or {}}, f, indent=2)

    log(f"Report written to {summary_md_path}", "INFO")
    return (summary_md_path, summary_json_path)
