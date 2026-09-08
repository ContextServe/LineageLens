#!/usr/bin/env python3
"""
Simple benchmark runner: MCP vs. no-MCP evaluation.
Config: benchmark.yaml
Usage: python run_benchmark.py --config benchmark.yaml
"""

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import yaml


def log(msg: str, level: str = "INFO", section: bool = False):
    """
    Timestamped logging with optional section headers.

    Args:
        msg: The message to log
        level: Log level (INFO, WARN, ERROR, DEBUG)
        section: If True, add visual separator for section headers
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    if section:
        print()
        print(f"{'=' * 80}")
        print(f"[{ts}] {level}: {msg}")
        print(f"{'=' * 80}")
    else:
        print(f"[{ts}] {level:7s} | {msg}")


def run_cmd(cmd, cwd=None, check=True, capture=False, timeout=None):
    """
    Run a shell command, optionally capturing output.

    Args:
        cmd: Command list
        cwd: Working directory
        check: Raise on non-zero exit
        capture: Capture stdout/stderr
        timeout: Timeout in seconds (default: 1800 to accommodate large clones/analysis)
    """
    if timeout is None:
        timeout = 1800  # 30 min default, much larger than the old 300s

    log(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
        if capture:
            return result.stdout, result.stderr, result.returncode
        return None, None, 0
    except subprocess.TimeoutExpired:
        log(f"Command timed out after {timeout}s: {' '.join(cmd)}", "ERROR")
        raise
    except Exception as e:
        log(f"Command failed: {e}", "ERROR")
        if check:
            raise
        return None, None, 1


def load_config(config_path: str) -> dict:
    """Load the YAML config file."""
    config_file = Path(config_path)
    if not config_file.exists():
        log(f"Config file not found: {config_path}", "ERROR")
        sys.exit(1)

    with open(config_file) as f:
        config = yaml.safe_load(f)

    log(f"Loaded config from {config_path}")
    return config


def resolve_pr(repo_url: str, pr_number: int, config: dict) -> dict:
    """
    Resolve PR info via 'gh pr view' if base_commit/expected_commit not set.
    Returns: {base_sha, merge_sha, title, body}
    """
    # Extract repo owner/name from URL
    # https://github.com/langchain-ai/langchain -> langchain-ai/langchain
    repo = "/".join(repo_url.rstrip('/').split('/')[-2:])

    log(f"Resolving PR #{pr_number} from {repo}")

    # gh pr view <nr> --repo <owner/repo> --json baseRefOid,mergeCommit,title,body
    stdout, _, returncode = run_cmd(
        [
            "gh", "pr", "view", str(pr_number),
            "--repo", repo,
            "--json", "baseRefOid,mergeCommit,title,body"
        ],
        capture=True
    )

    if returncode != 0:
        log(f"Failed to resolve PR #{pr_number}", "ERROR")
        sys.exit(1)

    pr_data = json.loads(stdout)

    base_sha = config.get("repo", {}).get("base_commit") or pr_data["baseRefOid"]

    # mergeCommit is an object with 'oid' field, or None if not merged
    merge_commit_obj = pr_data.get("mergeCommit")
    if isinstance(merge_commit_obj, dict):
        merge_sha = merge_commit_obj.get("oid")
    else:
        merge_sha = merge_commit_obj

    merge_sha = config.get("repo", {}).get("expected_commit") or merge_sha
    title = pr_data["title"]
    body = pr_data["body"] or ""

    log(f"Base commit: {base_sha[:8]}")
    if merge_sha:
        log(f"Merge commit: {merge_sha[:8]}")
    else:
        log("Merge commit: (PR not yet merged)", "WARN")

    return {
        "base_sha": base_sha,
        "merge_sha": merge_sha,
        "title": title,
        "body": body,
        "full_repo": repo
    }


def prepare_clones(work_dir: str, repo_url: str, base_sha: str, config: dict) -> tuple:
    """
    Create two clones (mcp_dir, nonmcp_dir) at the base commit.
    Returns: (mcp_clone_path, nonmcp_clone_path)
    """
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    mcp_dir = config.get("mcp_dir", "mcptest")
    nonmcp_dir = config.get("nonmcp_dir", "nonmcp_test")
    timeout = config.get("timeout_seconds", 900)

    mcp_clone = work_path / mcp_dir
    nonmcp_clone = work_path / nonmcp_dir

    # Remove existing clones if present
    for clone_path in [mcp_clone, nonmcp_clone]:
        if clone_path.exists():
            log(f"Removing existing {clone_path.name}")
            shutil.rmtree(clone_path)

    # Clone both repos at base commit
    # Use a longer timeout for clone (can take several minutes for large repos)
    clone_timeout = max(timeout * 2, 3600)  # At least 1 hour for clone
    for clone_path, name in [(mcp_clone, "mcp"), (nonmcp_clone, "nonmcp")]:
        log(f"Cloning {name} to {clone_path}")
        run_cmd(["git", "clone", repo_url, str(clone_path)], timeout=clone_timeout)
        run_cmd(["git", "checkout", base_sha], cwd=str(clone_path), timeout=timeout)

    log(f"Created two clones at {base_sha[:8]}")
    return str(mcp_clone), str(nonmcp_clone)


def extract_files_from_response(response_text: str) -> list:
    """
    Extract file paths from '## Files I would change' block.
    Returns: list of file paths (one per line after the marker)
    """
    files = []
    marker = "## Files I would change"
    if marker not in response_text:
        log(f"WARNING: No '{marker}' section found in response", "WARN")
        return files

    # Find the section
    idx = response_text.find(marker)
    section = response_text[idx + len(marker):]

    # Extract lines until we hit another ## or end of string
    lines = section.split('\n')
    for line in lines[1:]:  # Skip the header line
        line = line.strip()
        if line.startswith('##'):
            break
        if line and not line.startswith('#'):
            files.append(line)

    return files


def parse_claude_jsonl(jsonl_data: str, scenario: str) -> dict:
    """
    Parse JSONL output from claude -p --output-format stream-json --verbose.

    Real schema (confirmed by manual spike, not the Messages-API shape we
    originally guessed): each line is a top-level SDK event with a "type"
    field:
      - "system"    — init event (subtype "init"), no content
      - "assistant" — has "message": {"content": [...]}, content blocks are
                       {"type": "text", "text": ...} or
                       {"type": "tool_use", "name": ..., "input": {...}}
      - "user"      — tool_result echoes, not needed here
      - "result"    — final summary line: "total_cost_usd", "usage"
                       ({"input_tokens", "output_tokens", ...}), "result"
                       (the final response text), "is_error"

    Logs all tool invocations for validation.
    Returns: {cost_usd, tokens_in, tokens_out, tool_calls, response_text, is_error}
    """
    lines = jsonl_data.strip().split('\n')
    if not lines:
        return {}

    cost_usd = 0.0
    tokens_in = 0
    tokens_out = 0
    tool_calls = []
    response_text = ""
    is_error = False

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"Failed to parse JSONL line {i}: {e}", "WARN")
            continue

        event_type = obj.get("type")

        if event_type == "assistant":
            content = obj.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "text":
                    response_text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "unknown")
                    tool_input = block.get("input", {})
                    tool_calls.append({
                        "name": tool_name,
                        "input": tool_input
                    })
                    log(f"Tool call: {tool_name}", "DEBUG")
                    if isinstance(tool_input, dict):
                        for key, val in tool_input.items():
                            log(f"  {key}: {str(val)[:100]}", "DEBUG")

        elif event_type == "result":
            # Final summary line — authoritative cost/usage/result text
            cost_usd = obj.get("total_cost_usd", cost_usd)
            usage = obj.get("usage", {})
            tokens_in = usage.get("input_tokens", tokens_in)
            tokens_out = usage.get("output_tokens", tokens_out)
            is_error = obj.get("is_error", False)
            # "result" holds the model's final text if no text block was
            # captured from an "assistant" event (defensive fallback)
            if not response_text:
                response_text = obj.get("result", "")

    log(f"Scenario {scenario}: {len(tool_calls)} tool calls", "INFO")
    for tc in tool_calls:
        log(f"  - {tc['name']}", "DEBUG")

    if is_error:
        log(f"Scenario {scenario}: claude reported is_error=true", "WARN")

    return {
        "cost_usd": cost_usd,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tool_calls": tool_calls,
        "response_text": response_text,
        "is_error": is_error
    }


def run_claude_scenario(scenario: str, prompt: str, clone_path: str, config: dict, mcp_clone_path: str = None, results_dir: str = None) -> dict:
    """
    Run claude with either MCP or baseline tool restrictions.

    Args:
        scenario: 'mcp' or 'baseline'
        prompt: Full prompt text (rendered with PR metadata)
        clone_path: Path to the repo clone to work from
        config: Benchmark config dict
        mcp_clone_path: (MCP only) Path to MCP clone for graph access
        results_dir: Directory to persist raw JSONL transcript

    Returns: {
        scenario, cost_usd, tokens_in, tokens_out, duration_sec,
        tool_calls, source_files_read, files_identified, raw_response
    }
    """
    log(f"Running Claude Scenario: {scenario.upper()}", "INFO", section=True)
    start_time = time.time()

    model = config.get("model", "claude-sonnet-4-5")
    budget_usd = config.get("budget_usd", 3.00)
    timeout = config.get("timeout_seconds", 900)
    mcp_prefix = config.get("mcp_tool_prefix", "mcp__lineagelens")

    log(f"Working directory: {clone_path}")
    log(f"Model: {model}")
    log(f"Budget: ${budget_usd}")
    log(f"Timeout: {timeout}s")

    # Build claude command
    cmd = ["claude", "-p", prompt]

    if scenario == "mcp":
        # MCP scenario: strict tool restriction
        if not mcp_clone_path:
            log("ERROR: mcp_clone_path required for MCP scenario", "ERROR")
            return {}

        # Build MCP config
        mcp_config = {
            "mcpServers": {
                "lineagelens": {
                    "command": "lineagelens-mcp",
                    "env": {
                        "LINEAGELENS_PROJECT": str(Path(mcp_clone_path).absolute())
                    }
                }
            }
        }
        mcp_config_json = json.dumps(mcp_config)

        # NOTE: --strict-mcp-config is a bare boolean flag ("only use the
        # servers passed via --mcp-config, ignore everything else") — it
        # does NOT take the JSON itself as its value. Confirmed via
        # `claude --help`. Passing the JSON directly after
        # --strict-mcp-config (the original bug here) means --mcp-config is
        # never set, so *no* MCP server loads at all — Claude then reports
        # the lineagelens tools as simply not present, which is exactly
        # what the first real run showed (0 tool calls, $0 cost display
        # bug aside).
        # Confirmed by manual spike (2026-08-25): Claude calls
        # trigger_analysis unprompted as an early exploratory step even
        # though the prompt never mentions it. --allowedTools alone can't
        # stop this (it's an allow-glob, not a deny-list), so it must be
        # explicitly denied via --disallowedTools (confirmed real flag via
        # `claude --help`) — otherwise it silently re-runs Python-only
        # analysis in-process mid-session and could corrupt a pre-built
        # Java/JS graph.
        trigger_analysis_tool = f"{mcp_prefix}__trigger_analysis"

        cmd.extend([
            "--mcp-config", mcp_config_json,
            "--strict-mcp-config",
            "--allowedTools", f"{mcp_prefix}__*",
            "--disallowedTools", trigger_analysis_tool,
            "--model", model,
            "--max-budget-usd", str(budget_usd),
            "--output-format", "stream-json",
            "--verbose"
        ])

        log(f"Tool restriction: {mcp_prefix}__* (MCP tools only)", "INFO")
        log(f"Excluded: {trigger_analysis_tool} (would corrupt pre-built graph)", "INFO")

    else:
        # Baseline scenario: standard file exploration tools
        allowed_tools = "Read,Glob,Grep,Bash(find *),Bash(ls *)"
        cmd.extend([
            "--allowedTools", allowed_tools,
            "--model", model,
            "--max-budget-usd", str(budget_usd),
            "--output-format", "stream-json",
            "--verbose"
        ])

        log(f"Tool restriction: {allowed_tools}", "INFO")

    # Run claude
    log("Running: claude -p <prompt> [flags]", "INFO")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=timeout
        )

        duration = time.time() - start_time

        # **Always** persist the raw transcript, even on error (Gap B fix)
        if results_dir:
            try:
                results_path = Path(results_dir)
                results_path.mkdir(parents=True, exist_ok=True)
                transcript_path = results_path / f"{scenario}_stream.jsonl"
                with open(transcript_path, 'w') as f:
                    f.write(result.stdout)
                log(f"Transcript persisted to {transcript_path}", "DEBUG")
            except Exception as e:
                log(f"Failed to persist transcript: {e}", "WARN")

        # Parse JSONL output (even if returncode != 0, in case there's partial output)
        parsed = parse_claude_jsonl(result.stdout, scenario)

        # Log full stderr if there was an error (Gap C fix)
        if result.returncode != 0:
            log(f"Claude exited with code {result.returncode}", "WARN")
            log(f"stderr (full): {result.stderr}", "WARN")
            # Still continue — partial output may be parseable

        log(f"Duration: {duration:.1f}s", "INFO")
        log(f"Cost: ${parsed.get('cost_usd', 0):.2f}", "INFO")
        log(f"Tokens: {parsed.get('tokens_in', 0)} in, {parsed.get('tokens_out', 0)} out", "INFO")

        # Extract identified files
        response_text = parsed.get("response_text", "")
        files_identified = extract_files_from_response(response_text)
        log(f"Files identified: {len(files_identified)}", "INFO")

        # Check for source file reads in MCP scenario
        source_files_read = []
        if scenario == "mcp":
            for tc in parsed.get("tool_calls", []):
                tool_name = tc.get("name", "")
                tool_input = tc.get("input", {})
                # Check if any Read/Glob/Bash was used on source files
                if tool_name in ["Read", "Glob", "Bash"]:
                    file_arg = tool_input.get("path", "") or tool_input.get("pattern", "") or tool_input.get("command", "")
                    # Check for source file patterns
                    if re.search(r'\.(py|java|ts|tsx|js|jsx)$', str(file_arg)):
                        source_files_read.append({
                            "tool": tool_name,
                            "arg": file_arg
                        })
                        log(f"⚠️  VIOLATION: {tool_name} called on source file: {file_arg}", "WARN")

        return {
            "scenario": scenario,
            "cost_usd": parsed.get("cost_usd", 0.0),
            "tokens_in": parsed.get("tokens_in", 0),
            "tokens_out": parsed.get("tokens_out", 0),
            "duration_sec": duration,
            "tool_calls": parsed.get("tool_calls", []),
            "source_files_read": source_files_read,
            "files_identified": files_identified,
            "raw_response": response_text
        }

    except subprocess.TimeoutExpired:
        log(f"Claude call timed out after {timeout}s", "ERROR")
        return {}
    except Exception as e:
        log(f"Claude call failed: {e}", "ERROR")
        return {}


def get_ground_truth_files(clone_path: str, base_sha: str, merge_sha: str) -> set:
    """
    Get the ground truth list of changed files via git diff.
    Returns: set of relative file paths that were modified in the PR
    """
    if not merge_sha:
        log("WARNING: PR not merged, cannot determine ground truth", "WARN")
        return set()

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}...{merge_sha}"],
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            log(f"git diff failed: {result.stderr}", "WARN")
            return set()

        files = set(result.stdout.strip().split('\n'))
        files = {f for f in files if f.strip()}  # Remove empty strings
        return files
    except Exception as e:
        log(f"Failed to get ground truth: {e}", "ERROR")
        return set()


def normalize_path(path: str) -> str:
    """Normalize a path for comparison (strip leading/trailing whitespace, forward slashes)."""
    return path.strip().lstrip('./').replace('\\', '/')


def compute_metrics(identified: list, ground_truth: set) -> dict:
    """
    Compute precision, recall, F1 for file identification.

    Args:
        identified: List of file paths identified by Claude
        ground_truth: Set of files actually changed in the PR

    Returns: {precision, recall, f1, tp, fp, fn}
    """
    identified_normalized = {normalize_path(f) for f in identified}
    ground_truth_normalized = {normalize_path(f) for f in ground_truth}

    tp = len(identified_normalized & ground_truth_normalized)  # True positives
    fp = len(identified_normalized - ground_truth_normalized)  # False positives
    fn = len(ground_truth_normalized - identified_normalized)  # False negatives

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "identified_count": len(identified_normalized),
        "ground_truth_count": len(ground_truth_normalized)
    }


def write_report(results: list, ground_truth: set, work_dir: str, pr_num: int) -> tuple:
    """
    Write summary.md and summary.json reports.

    Args:
        results: List of scenario result dicts from run_claude_scenario()
        ground_truth: Set of files changed in the PR
        work_dir: Work directory for output
        pr_num: PR number for naming

    Returns: (summary_md_path, summary_json_path)
    """
    work_path = Path(work_dir)
    results_dir = work_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # Compute metrics for each scenario
    mcp_result = next((r for r in results if r.get("scenario") == "mcp"), {})
    baseline_result = next((r for r in results if r.get("scenario") == "baseline"), {})

    mcp_metrics = compute_metrics(mcp_result.get("files_identified", []), ground_truth)
    baseline_metrics = compute_metrics(baseline_result.get("files_identified", []), ground_truth)

    # Write summary.md
    summary_md_path = results_dir / "summary.md"
    with open(summary_md_path, 'w') as f:
        f.write(f"# Benchmark Results: PR #{pr_num}\n\n")
        f.write("## Overview\n\n")
        f.write("Comparison of LineageLens MCP vs. Baseline (file exploration) approaches.\n\n")

        f.write("## Results\n\n")
        f.write("| Metric | MCP | Baseline |\n")
        f.write("|--------|-----|----------|\n")
        f.write(f"| Cost (USD) | ${mcp_result.get('cost_usd', 0):.2f} | ${baseline_result.get('cost_usd', 0):.2f} |\n")
        f.write(f"| Tokens In | {mcp_result.get('tokens_in', 0):,} | {baseline_result.get('tokens_in', 0):,} |\n")
        f.write(f"| Tokens Out | {mcp_result.get('tokens_out', 0):,} | {baseline_result.get('tokens_out', 0):,} |\n")
        f.write(f"| Duration (s) | {mcp_result.get('duration_sec', 0):.1f} | {baseline_result.get('duration_sec', 0):.1f} |\n")
        f.write(f"| Files Identified | {mcp_metrics['identified_count']} | {baseline_metrics['identified_count']} |\n")
        f.write(f"| Precision | {mcp_metrics['precision']:.3f} | {baseline_metrics['precision']:.3f} |\n")
        f.write(f"| Recall | {mcp_metrics['recall']:.3f} | {baseline_metrics['recall']:.3f} |\n")
        f.write(f"| F1 Score | {mcp_metrics['f1']:.3f} | {baseline_metrics['f1']:.3f} |\n\n")

        # Ground truth
        f.write("## Ground Truth\n\n")
        f.write(f"Files changed in PR: {len(ground_truth)}\n\n")
        for fname in sorted(ground_truth):
            f.write(f"- {fname}\n")

        # MCP details
        f.write("\n## Scenario A: MCP-Tool-Only\n\n")
        f.write(f"Cost: ${mcp_result.get('cost_usd', 0):.2f}\n")
        f.write(f"Duration: {mcp_result.get('duration_sec', 0):.1f}s\n")
        f.write(f"Tool Calls: {len(mcp_result.get('tool_calls', []))}\n")
        if mcp_result.get('source_files_read'):
            f.write(f"⚠️  **SOURCE FILE READS DETECTED**: {len(mcp_result.get('source_files_read'))}\n")
            for sfr in mcp_result.get('source_files_read', []):
                f.write(f"  - {sfr['tool']}: {sfr['arg']}\n")
        else:
            f.write("✓ No source file reads detected (tool-only execution)\n")

        # Baseline details
        f.write("\n## Scenario B: Baseline (File Exploration)\n\n")
        f.write(f"Cost: ${baseline_result.get('cost_usd', 0):.2f}\n")
        f.write(f"Duration: {baseline_result.get('duration_sec', 0):.1f}s\n")
        f.write(f"Tool Calls: {len(baseline_result.get('tool_calls', []))}\n")

        # Comparison
        cost_ratio = mcp_result.get('cost_usd', 0) / max(baseline_result.get('cost_usd', 1), 0.01)
        time_ratio = mcp_result.get('duration_sec', 0) / max(baseline_result.get('duration_sec', 1), 0.01)
        f1_delta = mcp_metrics['f1'] - baseline_metrics['f1']

        f.write("\n## Summary\n\n")
        if cost_ratio < 1:
            f.write(f"✓ **MCP was {(1/cost_ratio):.1f}x cheaper** (${mcp_result.get('cost_usd', 0):.2f} vs ${baseline_result.get('cost_usd', 0):.2f})\n")
        else:
            f.write(f"• MCP cost was {cost_ratio:.1f}x baseline\n")

        if time_ratio < 1:
            f.write(f"✓ **MCP was {(1/time_ratio):.1f}x faster** ({mcp_result.get('duration_sec', 0):.1f}s vs {baseline_result.get('duration_sec', 0):.1f}s)\n")
        else:
            f.write(f"• MCP took {time_ratio:.1f}x baseline time\n")

        if f1_delta > 0:
            f.write(f"✓ **MCP F1 was +{f1_delta:.3f} points higher**\n")
        elif f1_delta < 0:
            f.write(f"• MCP F1 was {f1_delta:.3f} points lower\n")
        else:
            f.write("• F1 scores were equal\n")

    # Write summary.json
    summary_json_path = results_dir / "summary.json"
    summary_data = {
        "pr_number": pr_num,
        "ground_truth_files": sorted(ground_truth),
        "mcp": {
            "cost_usd": mcp_result.get("cost_usd", 0),
            "tokens_in": mcp_result.get("tokens_in", 0),
            "tokens_out": mcp_result.get("tokens_out", 0),
            "duration_sec": mcp_result.get("duration_sec", 0),
            "tool_calls": len(mcp_result.get("tool_calls", [])),
            "source_file_violations": len(mcp_result.get("source_files_read", [])),
            "metrics": mcp_metrics,
            "files_identified": mcp_result.get("files_identified", [])
        },
        "baseline": {
            "cost_usd": baseline_result.get("cost_usd", 0),
            "tokens_in": baseline_result.get("tokens_in", 0),
            "tokens_out": baseline_result.get("tokens_out", 0),
            "duration_sec": baseline_result.get("duration_sec", 0),
            "tool_calls": len(baseline_result.get("tool_calls", [])),
            "metrics": baseline_metrics,
            "files_identified": baseline_result.get("files_identified", [])
        }
    }

    with open(summary_json_path, 'w') as f:
        json.dump(summary_data, f, indent=2)

    log(f"Report written to {summary_md_path}", "INFO")
    log(f"Data written to {summary_json_path}", "INFO")

    return (str(summary_md_path), str(summary_json_path))


def load_and_render_prompt(scenario: str, pr_title: str, pr_body: str) -> str:
    """
    Load the appropriate prompt template (mcp or baseline) and render it
    with PR title/body.

    Args:
        scenario: "mcp" or "baseline"
        pr_title: PR title string
        pr_body: PR body string

    Returns:
        Rendered prompt string
    """
    script_dir = Path(__file__).parent
    if scenario == "mcp":
        prompt_file = script_dir / "prompt_mcp.md"
    else:
        prompt_file = script_dir / "prompt_baseline.md"

    if not prompt_file.exists():
        log(f"Prompt template not found: {prompt_file}", "ERROR")
        sys.exit(1)

    with open(prompt_file) as f:
        template = f.read()

    # Replace placeholders
    rendered = template.replace("{pr_title}", pr_title)
    rendered = rendered.replace("{pr_body}", pr_body)

    log(f"Loaded {scenario} prompt template from {prompt_file.name}")
    return rendered


def run_lineagelens_index(clone_path: str, source_roots: list, timeout: int = 900) -> bool:
    """Run ``lineagelens index`` in the clone.

    Schema 4 removed the ``init`` step and the config file. ``source_roots`` is
    accepted and ignored: language is detected per file and services come from
    build manifests, so there is nothing to declare -- and the old
    ``lineagelens.yaml`` patching this used to do would silently have no effect
    if it were kept.

    Returns True if the index was built and holds a plausible number of nodes.
    """
    clone = Path(clone_path)

    if source_roots:
        log(
            f"ignoring source_roots={source_roots}: schema 4 detects language "
            f"per file and needs no configuration"
        )

    log(f"Running lineagelens index in {clone.name}")
    # Indexing a large monorepo takes tens of seconds; allow generous headroom.
    index_timeout = max(timeout * 2, 1200)
    run_cmd(["lineagelens", "index", "."], cwd=str(clone), timeout=index_timeout)

    graph_file = clone / ".lineagelens" / "graph.sqlite"
    if not graph_file.exists():
        log(f"graph.sqlite not found at {graph_file}", "ERROR")
        return False

    conn = sqlite3.connect(str(graph_file))
    try:
        node_count = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT count(*) FROM edges").fetchone()[0]
        # Dangling endpoints are impossible by foreign key, but this is the
        # defect that made the schema-3 Dubbo graph 100% non-traversable while
        # reporting success, so the benchmark checks rather than assumes.
        dangling = conn.execute(
            "SELECT count(*) FROM edges e "
            "WHERE NOT EXISTS (SELECT 1 FROM nodes WHERE id = e.src) "
            "   OR NOT EXISTS (SELECT 1 FROM nodes WHERE id = e.dst)"
        ).fetchone()[0]
    finally:
        conn.close()

    log(f"Graph has {node_count:,} nodes and {edge_count:,} edges")

    if dangling:
        log(f"{dangling} edges have unresolvable endpoints", "ERROR")
        return False

    if node_count < 20:
        log(
            f"WARNING: only {node_count} nodes (expected >20). Check that the "
            f"clone actually contains source in a supported language; run "
            f"`lineagelens coverage` in the clone to see what was skipped.",
            "ERROR",
        )
        return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="LineageLens MCP vs. no-MCP benchmark runner"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="benchmark/benchmark.yaml",
        help="Path to config file (default: benchmark/benchmark.yaml)"
    )
    args = parser.parse_args()

    log("LineageLens Benchmark Runner - MCP vs. Baseline", "INFO", section=True)
    log(f"Starting benchmark pipeline with config: {args.config}")

    # Load config
    config = load_config(args.config)
    log("Configuration loaded successfully", "DEBUG")
    log(f"  Repository: {config['repo']['url']}")
    log(f"  PR Number: {config['repo']['pr']}")
    log(f"  Language: {config.get('language', 'python')}")
    log(f"  Work directory: {config['work_dir']}")
    log(f"  Model: {config.get('model', 'claude-sonnet-4-5')}")
    log(f"  Budget: ${config.get('budget_usd', '3.00')}")

    # Resolve PR
    log("Resolving PR metadata via GitHub API", "INFO", section=True)
    pr_info = resolve_pr(
        config["repo"]["url"],
        config["repo"]["pr"],
        config
    )
    log("PR metadata resolved successfully", "DEBUG")

    # Prepare clones
    log("Preparing git clones at base commit", "INFO", section=True)
    log(f"Creating clones under: {config['work_dir']}")
    mcp_clone, nonmcp_clone = prepare_clones(
        config["work_dir"],
        config["repo"]["url"],
        pr_info["base_sha"],
        config
    )
    log("Both clones created successfully", "DEBUG")

    # Run lineagelens init + analyze on mcp clone only
    log("Running LineageLens analysis on MCP clone", "INFO", section=True)
    source_roots = config.get("source_roots", [])
    timeout = config.get("timeout_seconds", 900)
    if source_roots:
        log(f"Patching source_roots: {source_roots}")
    log("Initializing LineageLens configuration...")
    log("Running code graph analysis (this may take a few minutes)...")
    success = run_lineagelens_index(mcp_clone, source_roots, timeout=timeout)

    if not success:
        log("FAILED: Unable to generate code graph on MCP clone", "ERROR", section=True)
        log("This may indicate:", "ERROR")
        log("  - Incorrect source_roots configuration (check your benchmark.yaml)")
        log("  - Repository layout incompatibility")
        log("  - LineageLens initialization failure")
        sys.exit(1)

    log("Prepare phase COMPLETE", "INFO", section=True)
    log(f"✓ MCP clone path: {mcp_clone}")
    log(f"✓ Non-MCP clone path: {nonmcp_clone}")
    log(f"✓ PR title: {pr_info['title']}")
    log(f"✓ Base commit: {pr_info['base_sha'][:8]}")
    log(f"✓ Merge commit: {pr_info['merge_sha'][:8] if pr_info['merge_sha'] else 'N/A (not merged)'}")

    # Test prompt loading (verify templates exist and render correctly)
    log("Loading prompt templates", "INFO", section=True)
    try:
        mcp_prompt = load_and_render_prompt("mcp", pr_info["title"], pr_info["body"])
        log(f"✓ MCP prompt loaded ({len(mcp_prompt)} chars)")
        baseline_prompt = load_and_render_prompt("baseline", pr_info["title"], pr_info["body"])
        log(f"✓ Baseline prompt loaded ({len(baseline_prompt)} chars)")
    except Exception as e:
        log(f"Failed to load prompts: {e}", "ERROR")
        sys.exit(1)

    # Get ground truth (files changed in PR)
    log("Determining ground truth (files changed in PR)", "INFO", section=True)
    ground_truth = get_ground_truth_files(nonmcp_clone, pr_info["base_sha"], pr_info["merge_sha"])
    log(f"Ground truth: {len(ground_truth)} files changed", "INFO")
    for fname in sorted(list(ground_truth)[:10]):
        log(f"  - {fname}", "DEBUG")
    if len(ground_truth) > 10:
        log(f"  ... and {len(ground_truth) - 10} more", "DEBUG")

    # Run Claude scenarios
    log("Claude Analysis Phase - Running both scenarios", "INFO", section=True)

    # Create results directory for transcripts
    results_dir = Path(config["work_dir"]) / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    results = []

    # Scenario A: MCP-only
    log("Scenario A: MCP-Tool-Only", "INFO", section=True)
    mcp_result = run_claude_scenario(
        "mcp",
        mcp_prompt,
        config["work_dir"],
        config,
        mcp_clone_path=mcp_clone,
        results_dir=str(results_dir)
    )
    if not mcp_result:
        log("FAILED: MCP scenario did not complete", "ERROR")
        sys.exit(1)
    results.append(mcp_result)

    # Scenario B: Baseline
    log("Scenario B: Baseline (File Exploration)", "INFO", section=True)
    baseline_result = run_claude_scenario(
        "baseline",
        baseline_prompt,
        nonmcp_clone,
        config,
        results_dir=str(results_dir)
    )
    if not baseline_result:
        log("FAILED: Baseline scenario did not complete", "ERROR")
        sys.exit(1)
    results.append(baseline_result)

    # Generate report
    log("Generating benchmark report", "INFO", section=True)
    try:
        summary_md, summary_json = write_report(
            results,
            ground_truth,
            config["work_dir"],
            config["repo"]["pr"]
        )
        log(f"✓ Report written to {summary_md}", "INFO")
        log(f"✓ Data written to {summary_json}", "INFO")
    except Exception as e:
        log(f"Failed to write report: {e}", "ERROR")
        sys.exit(1)

    # Final summary
    log("Benchmark COMPLETE", "INFO", section=True)
    log("✓ Prepare phase: DONE", "INFO")
    log("✓ Claude analysis (A & B): DONE", "INFO")
    log("✓ Scoring and report: DONE", "INFO")
    log("", "INFO")
    log(f"Results available at: {config['work_dir']}/results/", "INFO")
    log("", "INFO")


if __name__ == "__main__":
    main()
