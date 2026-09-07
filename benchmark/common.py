"""Shared utilities for multi-tool benchmark suite.

Relocates tool-agnostic functions from run_benchmark.py and adds
generalized/new utilities for N-arm benchmarking.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import Optional

from arms import Arm


def log(msg: str, level: str = "INFO", section: bool = False):
    """Timestamped logging with optional section headers."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    if section:
        print()
        print(f"{'=' * 80}")
        print(f"[{ts}] {level}: {msg}")
        print(f"{'=' * 80}")
    else:
        print(f"[{ts}] {level:7s} | {msg}")


def run_cmd(cmd, cwd=None, check=True, capture=False, timeout=None, env=None):
    """Run a shell command, optionally capturing output.

    Args:
        cmd: Command list
        cwd: Working directory
        check: Raise on non-zero exit
        capture: Capture stdout/stderr
        timeout: Timeout in seconds (default: 1800)
        env: Environment variables dict (default: os.environ)
    """
    if timeout is None:
        timeout = 1800  # 30 min default

    log(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            capture_output=capture,
            text=True,
            timeout=timeout,
            env=env or os.environ.copy(),
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

    with open(config_file, "r") as f:
        config = yaml.safe_load(f)

    # Back-compat shim: map old mcp_dir/nonmcp_dir to clone_dirs
    if "mcp_dir" in config or "nonmcp_dir" in config:
        log("Using legacy mcp_dir/nonmcp_dir keys; prefer clone_dirs in future", "WARN")
        clone_dirs = config.get("clone_dirs", {})
        if "mcp_dir" in config and "lineagelens" not in clone_dirs:
            clone_dirs["lineagelens"] = config["mcp_dir"]
        if "nonmcp_dir" in config and "baseline" not in clone_dirs:
            clone_dirs["baseline"] = config["nonmcp_dir"]
        config["clone_dirs"] = clone_dirs

    log(f"Loaded config from {config_path}")
    return config


def resolve_pr(repo_url: str, pr_number: int, config: dict) -> dict:
    """Resolve PR info via 'gh pr view' if base_commit/expected_commit not set.
    Returns: {base_sha, merge_sha, title, body}
    """
    # Extract repo owner/name from URL
    repo = "/".join(repo_url.rstrip("/").split("/")[-2:])

    log(f"Resolving PR #{pr_number} from {repo}")

    stdout, _, returncode = run_cmd(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "baseRefOid,mergeCommit,title,body",
        ],
        capture=True,
    )

    if returncode != 0:
        log(f"Failed to resolve PR #{pr_number}", "ERROR")
        sys.exit(1)

    pr_data = json.loads(stdout)

    base_sha = config.get("repo", {}).get("base_commit") or pr_data["baseRefOid"]

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

    return {"base_sha": base_sha, "merge_sha": merge_sha, "title": title, "body": body, "full_repo": repo}


def prepare_clones(
    work_dir: str,
    repo_url: str,
    base_sha: str,
    arm_names: list[str],
    clone_dir_map: dict[str, str] = None,
    timeout: int = 900,
    shallow: bool = False,
) -> dict[str, Path]:
    """Prepare N clones (one per arm) at the given base commit.

    Args:
        work_dir: Root work directory
        repo_url: Repository URL to clone
        base_sha: Base commit SHA to checkout
        arm_names: List of arm names to create clones for
        clone_dir_map: Dict mapping arm_name -> clone dir name (default: f"clone_{name}")
        timeout: Clone/checkout timeout in seconds
        shallow: If True, clone with --depth 1

    Returns:
        Dict mapping arm_name -> Path to its clone
    """
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)

    clones = {}
    clone_timeout = max(timeout * 2, 3600)

    for arm_name in arm_names:
        dir_name = clone_dir_map.get(arm_name, f"clone_{arm_name}") if clone_dir_map else f"clone_{arm_name}"
        clone_path = work_path / dir_name

        if clone_path.exists():
            log(f"Removing existing {clone_path.name}")
            shutil.rmtree(clone_path)

        depth_flag = ["--depth", "1"] if shallow else []
        run_cmd(["git", "clone"] + depth_flag + [repo_url, str(clone_path)], timeout=clone_timeout)
        run_cmd(["git", "checkout", base_sha], cwd=str(clone_path), timeout=timeout)

        clones[arm_name] = clone_path

    log(f"Created {len(arm_names)} clone(s) at {base_sha[:8]}")
    return clones


def run_arm_setup(arm: Arm, clone_path: Path, config: dict = None) -> bool:
    """Run an arm's setup commands and validate the result.

    Args:
        arm: The arm to set up
        clone_path: Path to the clone where setup runs
        config: Benchmark config dict (for extra env/timeout)

    Returns:
        True if setup succeeded (or was a no-op), False otherwise
    """
    if not arm.setup_cmds:
        log(f"Arm {arm.name}: no setup required")
        return True

    # Optional pre-setup hook
    if arm.pre_setup_hook:
        try:
            arm.pre_setup_hook(clone_path)
        except Exception as e:
            log(f"Pre-setup hook failed for {arm.name}: {e}", "ERROR")
            return False

    # Run setup commands
    env = os.environ.copy()
    if arm.setup_env:
        env.update(arm.setup_env)

    timeout = config.get("timeout_seconds", 900) if config else 900
    setup_timeout = max(timeout * 2, 1200)

    for cmd in arm.setup_cmds:
        try:
            run_cmd(cmd, cwd=str(clone_path), timeout=setup_timeout, env=env)
        except Exception as e:
            log(f"Setup command failed for {arm.name}: {e}", "ERROR")
            return False

    # Validate the setup
    if arm.validate_setup:
        try:
            valid = arm.validate_setup(clone_path)
            if not valid:
                log(f"Setup validation failed for {arm.name}", "ERROR")
                return False
        except Exception as e:
            log(f"Setup validation error for {arm.name}: {e}", "ERROR")
            return False

    log(f"Setup complete for {arm.name}")
    return True


def capture_tool_versions(arms: list[Arm]) -> dict[str, str]:
    """Capture version strings for all arms and the Claude CLI.

    Args:
        arms: List of arm objects to version

    Returns:
        Dict mapping tool_name -> version_string
    """
    versions = {}

    # Claude CLI version
    try:
        stdout, _, _ = run_cmd(["claude", "--version"], capture=True, check=False)
        versions["claude"] = stdout.strip()
    except Exception:
        versions["claude"] = "unknown"

    # Per-arm versions
    for arm in arms:
        if not arm.cli_binary:
            continue
        try:
            stdout, _, _ = run_cmd([arm.cli_binary, "--version"], capture=True, check=False)
            versions[arm.name] = stdout.strip()
        except Exception:
            versions[arm.name] = "unknown"

    return versions


def normalize_path(path: str) -> str:
    """Normalize a path for comparison."""
    return path.strip().lstrip("./").replace("\\", "/")


def compute_metrics(identified: list, ground_truth: set) -> dict:
    """Compute precision, recall, F1 for file identification.

    Args:
        identified: List of file paths identified
        ground_truth: Set of files actually changed in the PR

    Returns: {precision, recall, f1, tp, fp, fn, identified_count, ground_truth_count}
    """
    identified_normalized = {normalize_path(f) for f in identified}
    ground_truth_normalized = {normalize_path(f) for f in ground_truth}

    tp = len(identified_normalized & ground_truth_normalized)
    fp = len(identified_normalized - ground_truth_normalized)
    fn = len(ground_truth_normalized - identified_normalized)

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
        "ground_truth_count": len(ground_truth_normalized),
    }


def parse_claude_jsonl(jsonl_data: str, scenario: str) -> dict:
    """Parse JSONL output from claude -p --output-format stream-json --verbose.

    Schema:
      - "system": init event
      - "assistant": has "message": {"content": [...]}, content blocks are
        {"type": "text", "text": ...} or
        {"type": "tool_use", "name": ..., "input": {...}}
      - "result": final summary line with cost, usage, result

    Returns: {
        cost_usd, tokens_in, tokens_out, cache_read_tokens, cache_creation_tokens,
        tool_calls, response_text, is_error
    }
    """
    lines = jsonl_data.strip().split("\n")
    if not lines:
        return {}

    cost_usd = 0.0
    tokens_in = 0
    tokens_out = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
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
                    tool_calls.append({"name": tool_name, "input": tool_input})
                    log(f"Tool call: {tool_name}", "DEBUG")

        elif event_type == "result":
            cost_usd = obj.get("total_cost_usd", cost_usd)
            usage = obj.get("usage", {})
            tokens_in = usage.get("input_tokens", tokens_in)
            tokens_out = usage.get("output_tokens", tokens_out)
            cache_read_tokens = usage.get("cache_read_input_tokens", 0)
            cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            is_error = obj.get("is_error", False)
            if not response_text:
                response_text = obj.get("result", "")

    log(f"Scenario {scenario}: {len(tool_calls)} tool calls", "INFO")

    if is_error:
        log(f"Scenario {scenario}: claude reported is_error=true", "WARN")

    return {
        "cost_usd": cost_usd,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "tool_calls": tool_calls,
        "response_text": response_text,
        "is_error": is_error,
    }


def extract_files_from_response(response_text: str) -> list:
    """Extract file paths from '## Files I would change' block."""
    files = []
    marker = "## Files I would change"
    if marker not in response_text:
        log(f"WARNING: No '{marker}' section found in response", "WARN")
        return files

    idx = response_text.find(marker)
    section = response_text[idx + len(marker) :]

    lines = section.split("\n")
    for line in lines[1:]:
        line = line.strip()
        if line.startswith("##"):
            break
        if line and not line.startswith("#"):
            files.append(line)

    return files


def get_ground_truth_files(clone_path: str, base_sha: str, merge_sha: str) -> set:
    """Get the ground truth list of changed files via git diff."""
    if not merge_sha:
        log("WARNING: PR not merged, cannot determine ground truth", "WARN")
        return set()

    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}...{merge_sha}"],
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log(f"git diff failed: {result.stderr}", "WARN")
            return set()

        files = set(result.stdout.strip().split("\n"))
        files = {f for f in files if f.strip()}
        return files
    except Exception as e:
        log(f"Failed to get ground truth: {e}", "ERROR")
        return set()
