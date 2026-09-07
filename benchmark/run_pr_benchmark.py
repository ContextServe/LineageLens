#!/usr/bin/env python3
"""Suite A: PR-replication benchmark (multi-arm).

Clones a real repo at a PR's base commit, runs Claude with different code-graph tools,
and scores the file-list predictions against the hidden diff ground truth.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import yaml
from pathlib import Path

from arms import ARMS, Arm, build_mcp_config
from common import (
    extract_files_from_response,
    get_ground_truth_files,
    load_config,
    log,
    parse_claude_jsonl,
    prepare_clones,
    resolve_pr,
    run_arm_setup,
    capture_tool_versions,
)
from prompts import render_prompt
from report import write_pr_report


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


def run_claude_scenario(arm: Arm, prompt: str, clone_path: Path, config: dict, results_dir: Path) -> dict:
    """Run Claude with an arm's tool configuration.

    Returns: {
        "arm_name": str,
        "cost_usd": float,
        "tokens_in": int,
        "tokens_out": int,
        "cache_read_tokens": int,
        "cache_creation_tokens": int,
        "duration_sec": float,
        "tool_calls": list,
        "source_files_read": list (violations for MCP arms),
        "files_identified": list,
        "raw_response": str
    }
    """
    log(f"Running Claude with arm: {arm.name}", "INFO", section=True)
    start_time = time.time()

    # Build command
    mcp_config = build_mcp_config(arm, clone_path)
    cmd = build_claude_command(arm, prompt, clone_path, config, mcp_config)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=config.get("timeout_seconds", 900),
        )
        duration = time.time() - start_time

        # Persist transcript
        results_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = results_dir / f"{arm.name}_stream.jsonl"
        with open(transcript_path, "w") as f:
            f.write(result.stdout)

        # Parse output
        parsed = parse_claude_jsonl(result.stdout, arm.name)

        if result.returncode != 0:
            log(f"Claude exited with code {result.returncode}", "WARN")

        # Extract identified files
        files_identified = extract_files_from_response(parsed.get("response_text", ""))

        # Check for source-file reads (MCP arms only)
        source_files_read = []
        if arm.mcp_server and arm.prompt_source_files_regex:
            for tc in parsed.get("tool_calls", []):
                if tc.get("name") in ["Read", "Glob", "Bash"]:
                    file_arg = tc.get("input", {}).get("path", "")
                    if re.search(arm.prompt_source_files_regex, str(file_arg)):
                        source_files_read.append({"tool": tc["name"], "arg": file_arg})
                        log(f"⚠️  VIOLATION: {tc['name']} called on source file: {file_arg}", "WARN")

        return {
            "arm_name": arm.name,
            "cost_usd": parsed.get("cost_usd", 0.0),
            "tokens_in": parsed.get("tokens_in", 0),
            "tokens_out": parsed.get("tokens_out", 0),
            "cache_read_tokens": parsed.get("cache_read_tokens", 0),
            "cache_creation_tokens": parsed.get("cache_creation_tokens", 0),
            "duration_sec": duration,
            "tool_calls": parsed.get("tool_calls", []),
            "source_files_read": source_files_read,
            "files_identified": files_identified,
            "raw_response": parsed.get("response_text", ""),
        }

    except subprocess.TimeoutExpired:
        log(f"Claude call timed out after {config.get('timeout_seconds', 900)}s", "ERROR")
        return {}
    except Exception as e:
        log(f"Claude call failed: {e}", "ERROR")
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="LineageLens PR-replication benchmark (multi-arm)",
    )
    parser.add_argument("--config", type=str, default="benchmark/benchmark.yaml", help="Config file path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render prompts and show commands without running Claude",
    )
    args = parser.parse_args()

    log("LineageLens PR-Replication Benchmark — Multi-Arm", "INFO", section=True)

    config = load_config(args.config)
    arms_list = [ARMS[name] for name in config.get("arms", ["lineagelens", "baseline"])]

    log(f"Configuration loaded: {len(arms_list)} arms", "DEBUG")
    for arm in arms_list:
        log(f"  - {arm.name}", "DEBUG")

    # Resolve PR metadata
    log("Resolving PR metadata via GitHub API", "INFO", section=True)
    pr_info = resolve_pr(config["repo"]["url"], config["repo"]["pr"], config)
    log(f"PR metadata resolved successfully", "DEBUG")

    # Load prompts (needed for both dry-run and full run)
    log("Loading prompt templates", "INFO", section=True)
    prompts = {}
    for arm in arms_list:
        prompt_path = Path(__file__).parent / "prompt_pr_template.md"
        prompts[arm.name] = render_prompt(
            prompt_path,
            arm,
            suite="pr",
            pr_title=pr_info["title"],
            pr_body=pr_info["body"],
            suite_name="Scenario: " + arm.name,
        )
        log(f"✓ {arm.name} prompt loaded ({len(prompts[arm.name])} chars)")

    if args.dry_run:
        # Show rendered prompts and commands without executing
        log("DRY-RUN MODE: Showing prompts and commands without executing", "INFO", section=True)
        for arm in arms_list:
            log(f"\n{'='*80}", "INFO")
            log(f"Arm: {arm.name}", "INFO")
            log(f"{'='*80}", "INFO")
            log(f"Prompt (first 500 chars):\n{prompts[arm.name][:500]}\n...", "INFO")

            # Use dummy clone path for dry-run (won't be used, just for command construction)
            dummy_clone_path = Path(config["work_dir"]) / "dummy"
            mcp_config = build_mcp_config(arm, dummy_clone_path)
            cmd = build_claude_command(arm, prompts[arm.name], dummy_clone_path, config, mcp_config)
            log(f"Claude command:\n{' '.join(cmd)}\n", "INFO")

        log("Dry-run complete; no clones or API calls made.", "INFO")
        return

    # Prepare clones (one per arm)
    log("Preparing git clones at base commit", "INFO", section=True)
    clone_dir_map = config.get("clone_dirs", {})
    clones = prepare_clones(
        config["work_dir"],
        config["repo"]["url"],
        pr_info["base_sha"],
        [arm.name for arm in arms_list],
        clone_dir_map,
        timeout=config.get("timeout_seconds", 900),
        shallow=False,
    )
    log(f"Clones created successfully", "DEBUG")

    # Setup each arm
    log("Running setup for all arms", "INFO", section=True)
    for arm in arms_list:
        # Special: patch LineageLens source_roots if provided
        if arm.name == "lineagelens" and config.get("source_roots"):
            log(f"Patching lineagelens.yaml with source_roots: {config['source_roots']}")
            config_file = clones[arm.name] / "lineagelens.yaml"
            with open(config_file, "r") as f:
                ll_config = yaml.safe_load(f)
            if ll_config is None:
                ll_config = {}
            ll_config["source_roots"] = config["source_roots"]
            with open(config_file, "w") as f:
                yaml.dump(ll_config, f)

        success = run_arm_setup(arm, clones[arm.name], config)
        if not success:
            log(f"Setup failed for arm {arm.name}", "ERROR")
            sys.exit(1)

    # Get ground truth (files changed in PR)
    log("Determining ground truth (files changed in PR)", "INFO", section=True)
    ground_truth = get_ground_truth_files(clones[arms_list[0].name], pr_info["base_sha"], pr_info["merge_sha"])
    log(f"Ground truth: {len(ground_truth)} files changed", "INFO")
    for fname in sorted(list(ground_truth)[:10]):
        log(f"  - {fname}", "DEBUG")
    if len(ground_truth) > 10:
        log(f"  ... and {len(ground_truth) - 10} more", "DEBUG")

    # Run Claude scenarios
    log("Claude Analysis Phase - Running all arms", "INFO", section=True)

    results_dir = Path(config["work_dir"]) / "results"
    results = []

    for arm in arms_list:
        result = run_claude_scenario(arm, prompts[arm.name], clones[arm.name], config, results_dir)
        if not result:
            log(f"FAILED: arm {arm.name} did not complete", "ERROR")
            sys.exit(1)
        results.append(result)

    # Capture tool versions for reproducibility
    versions = capture_tool_versions(arms_list)

    # Generate report
    log("Generating benchmark report", "INFO", section=True)
    try:
        summary_md, summary_json = write_pr_report(
            results,
            arms_list,
            ground_truth,
            config["work_dir"],
            config["repo"]["pr"],
            versions,
        )
        log(f"✓ Report written to {summary_md}", "INFO")
        log(f"✓ Data written to {summary_json}", "INFO")
    except Exception as e:
        log(f"Failed to write report: {e}", "ERROR")
        sys.exit(1)

    # Final summary
    log("Benchmark COMPLETE", "INFO", section=True)
    log("✓ Prepare phase: DONE", "INFO")
    log("✓ Claude analysis (all arms): DONE", "INFO")
    log("✓ Scoring and report: DONE", "INFO")
    log("", "INFO")
    log(f"Results available at: {config['work_dir']}/results/", "INFO")
    log("", "INFO")


if __name__ == "__main__":
    main()
