#!/usr/bin/env python3
"""Suite B: Architecture-Q&A efficiency benchmark (multi-arm, CodeGraph-style).

Measures tool overhead (tool calls, tokens, cost, time) across multiple runs
per arm, using fixed architecture questions. Includes anti-contamination
layer to ensure tools are only reached via MCP, not Bash.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from arms import ARMS, Arm, build_mcp_config
from common import (
    load_config,
    log,
    parse_claude_jsonl,
    prepare_clones,
    run_arm_setup,
    capture_tool_versions,
)
from contamination import build_sanitized_path, parse_run_for_contamination
from prompts import render_prompt
from report import write_arch_report


def build_claude_command(arm: Arm, prompt: str, clone_path: Path, config: dict, mcp_config: dict = None) -> list:
    """Build the exact 'claude -p ...' command for an arm."""
    cmd = ["claude", "-p", prompt]

    if mcp_config:
        cmd.extend(
            [
                "--mcp-config",
                json.dumps(mcp_config),
                "--strict-mcp-config",
                "--allowedTools",
                arm.allowed_tools,
            ]
        )
        if arm.disallowed_tools:
            cmd.extend(["--disallowedTools", arm.disallowed_tools])
    else:
        cmd.extend(["--allowedTools", arm.allowed_tools])

    cmd.extend(
        [
            "--model",
            config.get("model", "claude-sonnet-4-5"),
            "--max-budget-usd",
            str(config.get("budget_usd", 3.00)),
            "--output-format",
            "stream-json",
            "--verbose",
        ]
    )

    return cmd


def run_claude_with_contamination_check(
    arm: Arm,
    prompt: str,
    clone_path: Path,
    config: dict,
    sanitized_path: str = None,
    results_dir: Path = None,
) -> dict:
    """Run Claude with optional contamination guards (sanitized PATH).

    Returns: {
        "arm_name": str,
        "tool_calls_count": int,
        "duration_sec": float,
        "file_reads_count": int,
        "tokens_in": int,
        "tokens_out": int,
        "cost_usd": float,
        "contamination_violations": list,
        "raw_response": str
    }
    """
    log(f"Running Claude for arm {arm.name}", "INFO")
    start_time = time.time()

    # Build command
    mcp_config = build_mcp_config(arm, clone_path)
    cmd = build_claude_command(arm, prompt, clone_path, config, mcp_config)

    # Set up environment with optional sanitized PATH
    env = os.environ.copy()
    if sanitized_path:
        env["PATH"] = sanitized_path

    try:
        result = subprocess.run(
            cmd,
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=config.get("timeout_seconds", 900),
            env=env,
        )
        duration = time.time() - start_time

        # Persist transcript
        if results_dir:
            results_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = results_dir / f"{arm.name}_stream.jsonl"
            with open(transcript_path, "w") as f:
                f.write(result.stdout)

        # Parse output
        parsed = parse_claude_jsonl(result.stdout, arm.name)

        # Count file reads (Read, Glob tools)
        file_reads_count = 0
        for tc in parsed.get("tool_calls", []):
            if tc.get("name") in ["Read", "Glob"]:
                file_reads_count += 1

        # Check for contamination violations
        contamination_violations = []
        if results_dir:
            transcript_path = results_dir / f"{arm.name}_stream.jsonl"
            if transcript_path.exists():
                exclude_binaries = [
                    a.cli_binary for a in [ARMS.get(n) for n in config.get("arms", [])]
                    if a and a.cli_binary and a.name != arm.name
                ]
                if exclude_binaries:
                    contamination_violations = parse_run_for_contamination(transcript_path, exclude_binaries)

        return {
            "arm_name": arm.name,
            "tool_calls_count": len(parsed.get("tool_calls", [])),
            "duration_sec": duration,
            "file_reads_count": file_reads_count,
            "tokens_in": parsed.get("tokens_in", 0),
            "tokens_out": parsed.get("tokens_out", 0),
            "cost_usd": parsed.get("cost_usd", 0.0),
            "contamination_violations": contamination_violations,
            "raw_response": parsed.get("response_text", ""),
        }

    except subprocess.TimeoutExpired:
        log(f"Claude call timed out after {config.get('timeout_seconds', 900)}s", "ERROR")
        return {}
    except Exception as e:
        log(f"Claude call failed: {e}", "ERROR")
        return {}


def aggregate_medians(results_by_arm: dict[str, list[dict]], repo_cfg: dict) -> dict:
    """Compute medians across N runs per arm.

    Args:
        results_by_arm: Dict mapping arm_name -> list of run results
        repo_cfg: Repo config dict

    Returns:
        Aggregated dict with medians
    """
    aggregated = {
        "repo": repo_cfg["url"],
        "question": repo_cfg["question"],
        "arms": {},
    }

    for arm_name, runs in results_by_arm.items():
        if not runs:
            aggregated["arms"][arm_name] = {"status": "no_data"}
            continue

        metrics = {
            "tool_calls": [r.get("tool_calls_count", 0) for r in runs],
            "duration_sec": [r.get("duration_sec", 0) for r in runs],
            "file_reads": [r.get("file_reads_count", 0) for r in runs],
            "tokens": [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in runs],
            "cost_usd": [r.get("cost_usd", 0) for r in runs],
        }

        aggregated["arms"][arm_name] = {
            "median_tool_calls": statistics.median(metrics["tool_calls"]),
            "median_duration_sec": statistics.median(metrics["duration_sec"]),
            "median_file_reads": statistics.median(metrics["file_reads"]),
            "median_tokens": statistics.median(metrics["tokens"]),
            "median_cost_usd": statistics.median(metrics["cost_usd"]),
            "runs_count": len(runs),
        }

    return aggregated


def main():
    parser = argparse.ArgumentParser(
        description="Architecture-Q&A efficiency benchmark (multi-arm, CodeGraph-style)",
    )
    parser.add_argument("--config", type=str, default="benchmark/arch_benchmark.yaml", help="Config file path")
    args = parser.parse_args()

    log("LineageLens Architecture-Q&A Benchmark — Multi-Arm", "INFO", section=True)

    config = load_config(args.config)
    arms_list = [ARMS[name] for name in config.get("arms", ["lineagelens", "baseline"])]
    runs_per_arm = config.get("runs_per_arm", 4)

    log(f"Configuration loaded: {len(arms_list)} arms × {runs_per_arm} runs per repo", "DEBUG")

    all_aggregated = {}

    # Per repo
    for repo_cfg in config.get("repos", []):
        log(f"Benchmarking repo: {repo_cfg['url']}", "INFO", section=True)

        # Prepare clones (one per arm, shallow since we don't need git history)
        clones = prepare_clones(
            config["work_dir"],
            repo_cfg["url"],
            "HEAD",
            [arm.name for arm in arms_list],
            shallow=True,
        )

        # Setup each arm
        for arm in arms_list:
            success = run_arm_setup(arm, clones[arm.name], config)
            if not success:
                log(f"Setup failed for arm {arm.name} on {repo_cfg['url']}", "ERROR")
                continue

        # Load prompt
        prompt_template_path = Path(__file__).parent / "prompt_arch_template.md"

        # Run N repetitions per arm
        results_by_arm = {}
        for arm in arms_list:
            log(f"Running {runs_per_arm} runs for arm: {arm.name}", "INFO")

            prompt = render_prompt(
                prompt_template_path,
                arm,
                suite="arch",
                repo_name=repo_cfg.get("name", repo_cfg["url"]),
                question=repo_cfg["question"],
                suite_name="Scenario: " + arm.name,
            )

            runs = []
            for run_idx in range(runs_per_arm):
                # Set up contamination-blocking environment (arch suite only)
                sanitized_path = None
                results_dir = None
                if arm.name != "baseline":
                    exclude_binaries = [
                        a.cli_binary
                        for a in arms_list
                        if a.cli_binary and a.name != arm.name
                    ]
                    if exclude_binaries:
                        sanitized_path = build_sanitized_path(exclude_binaries)
                    results_dir = Path(config["work_dir"]) / "results" / repo_cfg["url"].split("/")[-1]

                # Run Claude with contamination guards
                result = run_claude_with_contamination_check(
                    arm,
                    prompt,
                    clones[arm.name],
                    config,
                    sanitized_path,
                    results_dir,
                )

                if result:
                    runs.append(result)
                    log(
                        f"  Run {run_idx + 1} complete: {result.get('tool_calls_count', 0)} tool calls, "
                        f"{result.get('duration_sec', 0):.1f}s, {result.get('cost_usd', 0):.2f} USD",
                        "INFO",
                    )

                    if result.get("contamination_violations"):
                        log(f"  ⚠️  Contamination violations detected: {len(result['contamination_violations'])}", "WARN")

            results_by_arm[arm.name] = runs

        # Aggregate medians
        aggregated = aggregate_medians(results_by_arm, repo_cfg)
        all_aggregated[repo_cfg["url"]] = aggregated

    # Capture versions
    versions = capture_tool_versions(arms_list)

    # Generate report
    log("Generating architecture benchmark report", "INFO", section=True)
    try:
        summary_md, summary_json = write_arch_report(
            all_aggregated,
            arms_list,
            config["work_dir"],
            versions,
        )
        log(f"✓ Report written to {summary_md}", "INFO")
        log(f"✓ Data written to {summary_json}", "INFO")
    except Exception as e:
        log(f"Failed to write report: {e}", "ERROR")
        sys.exit(1)

    # Final summary
    log("Architecture Benchmark COMPLETE", "INFO", section=True)
    log(f"Results available at: {config['work_dir']}/results/", "INFO")


if __name__ == "__main__":
    main()
