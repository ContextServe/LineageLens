#!/usr/bin/env python3
"""
Simple benchmark runner: MCP vs. no-MCP evaluation.
Config: benchmark.yaml
Usage: python run_benchmark.py --config benchmark.yaml
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
import yaml
import tempfile
import shutil


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


def run_cmd(cmd, cwd=None, check=True, capture=False):
    """Run a shell command, optionally capturing output."""
    log(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=check,
            capture_output=capture,
            text=True,
            timeout=300
        )
        if capture:
            return result.stdout, result.stderr, result.returncode
        return None, None, 0
    except subprocess.TimeoutExpired:
        log(f"Command timed out: {' '.join(cmd)}", "ERROR")
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

    with open(config_file, 'r') as f:
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

    mcp_clone = work_path / mcp_dir
    nonmcp_clone = work_path / nonmcp_dir

    # Remove existing clones if present
    for clone_path in [mcp_clone, nonmcp_clone]:
        if clone_path.exists():
            log(f"Removing existing {clone_path.name}")
            shutil.rmtree(clone_path)

    # Clone both repos at base commit
    for clone_path, name in [(mcp_clone, "mcp"), (nonmcp_clone, "nonmcp")]:
        log(f"Cloning {name} to {clone_path}")
        run_cmd(["git", "clone", repo_url, str(clone_path)])
        run_cmd(["git", "checkout", base_sha], cwd=str(clone_path))

    log(f"Created two clones at {base_sha[:8]}")
    return str(mcp_clone), str(nonmcp_clone)


def run_lineagelens_init_analyze(clone_path: str, source_roots: list) -> bool:
    """
    Run 'lineagelens init' then 'lineagelens analyze' in the mcp clone.
    If source_roots is provided, patch lineagelens.yaml first.
    Returns: True if successful and graph.json has symbols, False otherwise.
    """
    clone = Path(clone_path)

    log(f"Running lineagelens init in {clone.name}")
    run_cmd(["lineagelens", "init", "."], cwd=str(clone))

    # If source_roots provided, patch the config
    if source_roots:
        log(f"Patching lineagelens.yaml with source_roots: {source_roots}")
        config_file = clone / "lineagelens.yaml"
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        if config is None:
            config = {}
        config['source_roots'] = source_roots
        with open(config_file, 'w') as f:
            yaml.dump(config, f)

    log(f"Running lineagelens analyze in {clone.name}")
    run_cmd(["lineagelens", "analyze", ".", "--quiet"], cwd=str(clone))

    # Check for graph.json and validate it's not empty
    graph_file = clone / ".lineagelens" / "graph.json"
    if not graph_file.exists():
        log(f"graph.json not found at {graph_file}", "ERROR")
        return False

    with open(graph_file, 'r') as f:
        graph = json.load(f)

    symbol_count = len(graph.get("symbols", []))
    log(f"Graph has {symbol_count} symbols")

    if symbol_count < 20:
        log(
            f"WARNING: graph.json has only {symbol_count} symbols (expected >20). "
            f"This may indicate an incorrect source_roots configuration. "
            f"Check your config's source_roots setting (e.g., for langchain, use "
            f"['libs/langchain/langchain']).",
            "ERROR"
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
    log(f"Configuration loaded successfully", "DEBUG")
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
    log(f"PR metadata resolved successfully", "DEBUG")

    # Prepare clones
    log("Preparing git clones at base commit", "INFO", section=True)
    log(f"Creating clones under: {config['work_dir']}")
    mcp_clone, nonmcp_clone = prepare_clones(
        config["work_dir"],
        config["repo"]["url"],
        pr_info["base_sha"],
        config
    )
    log(f"Both clones created successfully", "DEBUG")

    # Run lineagelens init + analyze on mcp clone only
    log("Running LineageLens analysis on MCP clone", "INFO", section=True)
    source_roots = config.get("source_roots", [])
    if source_roots:
        log(f"Patching source_roots: {source_roots}")
    log("Initializing LineageLens configuration...")
    log("Running code graph analysis (this may take a few minutes)...")
    success = run_lineagelens_init_analyze(mcp_clone, source_roots)

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

    # Summary
    log("Benchmark setup complete and validated", "INFO", section=True)
    log("Next steps:")
    log("  1. Review the clones at the paths above")
    log("  2. Verify graph.json was created in MCP clone")
    log("  3. Run Stage 5A & 5B: Claude analysis on both clones")
    log("", "INFO")


if __name__ == "__main__":
    main()
