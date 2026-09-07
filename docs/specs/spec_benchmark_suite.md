# Spec: Multi-Tool Benchmark Suite — LineageLens vs. CodeGraph vs. Graphify

**Status:** Proposed  
**Target Branch:** `feature/benchmark-suite`  
**Issue:** to be opened  

---

## Executive Summary

Extend LineageLens's existing PR-replication benchmark harness from a hardcoded 2-arm design
(MCP vs. baseline file exploration) to a pluggable N-arm architecture, enabling fair,
reproducible comparison against competing code-graph tools **CodeGraph** and **Graphify**. In
parallel, add a new architecture-question efficiency suite (modeled on CodeGraph's own published
methodology) to measure tool overhead, token costs, and runtime. Both suites share a unified
arm registry, so adding a new tool in the future is one registry entry, not a new code branch
or prompt file. Fix the live `prompt_mcp.md` tool-name bug (references non-existent tools like
`query_code_graph`; real tools are `search_symbols`, `get_callers`, `get_lineage`, etc.) by
driving prompt generation from the registry instead of hand-typed prose.

---

## Problem Statement

### 1. Current Harness is 2-Arm Hardcoded

`benchmark/run_benchmark.py` (~906 lines) has an `if scenario == "mcp": ... else: ...` branch
baked in at three critical junctures:
- `run_claude_scenario`: tool config, MCP server setup, allowed/disallowed tools, etc.
- `prepare_clones`: exactly two clones, `mcp_dir` and `nonmcp_dir`.
- `write_report`: hardcoded lookup of `scenario == "mcp"` and `scenario == "baseline"` for
  side-by-side 2-column markdown table + JSON output.

Adding CodeGraph or Graphify requires editing this function directly — no registry, no
extension point. This is precisely the problem we're fixing.

### 2. Tool-Name Bug in `prompt_mcp.md`

The MCP prompt tells Claude to call:
- `query_code_graph`
- `get_symbol_references`
- `get_symbol_callers`

**None of these tools exist** in `src/lineagelens/mcp_server.py`. The real registered MCP tools
(19 total, under the `mcp__lineagelens__` prefix) are:

```
get_symbol, search_symbols, get_callers, get_callees, get_lineage, get_impact,
list_implementations, list_providers, get_module_overview, list_entry_points,
list_risks, get_project_config, get_metrics, find_entry_points,
get_module_dependencies, list_dead_code, get_reachability, list_duplicate_names,
trigger_analysis, list_fields_by_type
```

This mismatch has likely undermined every past MCP scenario run — Claude's tool calls would
silently fail or hallucinate, and the harness would report successful analysis when none
actually occurred. This bug must be fixed at the root (tool names in the registry, not
hard-typed in prose) so it cannot recur.

### 3. No Reproducibility Metadata

Tool versions (`lineagelens --version`, `codegraph --version`, `graphify --version`, `claude`
CLI version) are not recorded. Re-running a benchmark months later against updated tools would
silently diverge with no record of why — undermining the "reproducible" claim.

---

## Solution Design

### 1. New File: `benchmark/arms.py` — Pluggable Arm Registry

Define an `Arm` dataclass capturing everything tool-specific:

```python
from dataclasses import dataclass, field
from typing import Callable, Optional
from pathlib import Path

@dataclass
class Arm:
    """Describes a code-graph tool (or baseline) as a benchmark 'arm' (contestant)."""
    
    # Identity
    name: str                              # "lineagelens" | "codegraph" | "graphify" | "baseline"
    
    # CLI binary name for anti-contamination (arch suite only)
    cli_binary: Optional[str]              # e.g. "lineagelens", "codegraph", "graphify"; None for baseline
    
    # Setup phase: commands to run once per clone
    setup_cmds: list[list[str]]            # e.g. [["lineagelens","init","."], ["lineagelens","analyze",".","--quiet"]]
    setup_env: dict[str, str] = field(default_factory=dict)  # extra env vars for setup_cmds
    
    # Pre-setup hook: arm-specific customization (e.g., LineageLens source_roots patching)
    pre_setup_hook: Optional[Callable[[Path], None]] = None  # Signature: hook(clone_path: Path) -> None
    
    # Validation: guard against misconfiguration (e.g., empty graph)
    validate_setup: Optional[Callable[[Path], bool]] = None  # Signature: validator(clone_path: Path) -> bool
    
    # MCP configuration (None for baseline, which uses no MCP)
    mcp_server: Optional[dict] = None      # e.g. {"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"<path>"}}
    
    # Tool access restrictions
    allowed_tools: str                     # e.g. "mcp__lineagelens__*" or "Read,Glob,Grep,Bash(find *),Bash(ls *)"
    disallowed_tools: Optional[str] = None # e.g. "mcp__lineagelens__trigger_analysis"; None = no restrictions
    
    # Extra env vars to set when invoking Claude with this arm's tools
    extra_env: dict[str, str] = field(default_factory=dict)  # e.g. {"CODEGRAPH_MCP_TOOLS": "1"}
    
    # Tool names, for documentation and prompt generation
    tool_names: list[str] = field(default_factory=list)  # e.g. ["get_symbol", "search_symbols", "get_callers", ...]
    
    # Natural-language usage hint block, rendered into prompt templates
    tool_hints: str = ""                   # e.g. "Use search_symbols to find a symbol...", empty for baseline
    
    # Regex for detecting raw-source-file reads (MCP-only arms); None disables the check
    prompt_source_files_regex: Optional[str] = r'\.(py|java|ts|tsx|js|jsx)$'
```

#### Concrete Arm Registrations

```python
ARM_LINEAGELENS = Arm(
    name="lineagelens",
    cli_binary="lineagelens",
    setup_cmds=[
        ["lineagelens", "init", "."],
        ["lineagelens", "analyze", ".", "--quiet"]
    ],
    pre_setup_hook=patch_lineagelens_source_roots,  # Custom hook to patch lineagelens.yaml
    validate_setup=validate_lineagelens_graph,       # Check graph.json has ≥20 symbols
    mcp_server={
        "command": "lineagelens-mcp",
        "env": {"LINEAGELENS_PROJECT": "<abs_clone_path>"}  # Path templated in at runtime
    },
    allowed_tools="mcp__lineagelens__*",
    disallowed_tools="mcp__lineagelens__trigger_analysis",  # Claude calls unprompted; would corrupt pre-built graph
    tool_names=[
        "get_symbol", "search_symbols", "get_callers", "get_callees", "get_lineage", "get_impact",
        "list_implementations", "list_providers", "get_module_overview", "list_entry_points",
        "list_risks", "get_project_config", "get_metrics", "find_entry_points",
        "get_module_dependencies", "list_dead_code", "get_reachability", "list_duplicate_names",
        "trigger_analysis", "list_fields_by_type"
    ],
    tool_hints="""Use search_symbols to find relevant classes/functions. Use get_callers and get_callees
to trace dependencies. Use get_lineage to understand inheritance chains. Use get_impact to see
what code changes would affect. Use get_module_overview to understand a module's entry points.""",
    prompt_source_files_regex=r'\.(py|java|ts|tsx|js|jsx)$'
)

ARM_CODEGRAPH = Arm(
    name="codegraph",
    cli_binary="codegraph",
    setup_cmds=[["codegraph", "init"]],
    validate_setup=validate_codegraph_index,  # Check .codegraph/ dir is non-empty
    mcp_server={
        "type": "stdio",
        "command": "codegraph",
        "args": ["serve", "--mcp"]
    },
    extra_env={"CODEGRAPH_MCP_TOOLS": "1"},  # Unlock tools beyond codegraph_explore
    allowed_tools="mcp__codegraph__*",
    tool_names=[
        "codegraph_explore", "codegraph_node", "codegraph_search", "codegraph_callers",
        "codegraph_callees", "codegraph_impact", "codegraph_files", "codegraph_status"
    ],
    tool_hints="""Use codegraph_explore to answer 'how does X work' and 'how does X reach Y' questions.
Use codegraph_impact to see the blast radius of changes. Use codegraph_callers and codegraph_callees
to trace call chains. codegraph_explore is the main tool; others provide lower-level detail.""",
    prompt_source_files_regex=r'\.(ts|tsx|js|jsx|py|java)$'
)

ARM_GRAPHIFY = Arm(
    name="graphify",
    cli_binary="graphify",
    setup_cmds=[["graphify", "extract", ".", "--code-only"]],  # Deterministic, API-key-free CLI path
    validate_setup=validate_graphify_graph,  # Check graphify-out/graph.json exists
    mcp_server={
        "command": "python",
        "args": ["-m", "graphify.serve", "graphify-out/graph.json"]
    },
    allowed_tools="mcp__graphify__*",
    tool_names=[
        "query_graph", "get_node", "get_neighbors", "shortest_path",
        "list_prs", "get_pr_impact", "triage_prs"  # PR-specific tools for Suite A
    ],
    tool_hints="""Use query_graph to find relevant symbols and relationships. Use get_node to inspect
a single symbol. Use shortest_path to understand how two concepts connect. For PR analysis, use
list_prs to enumerate pull requests, get_pr_impact to see which files a PR changes, and triage_prs
to assess PR scope.""",
    prompt_source_files_regex=r'\.(py|java|ts|tsx|js|jsx)$'
)

ARM_BASELINE = Arm(
    name="baseline",
    cli_binary=None,
    setup_cmds=[],
    mcp_server=None,
    allowed_tools="Read,Glob,Grep,Bash(find *),Bash(ls *)",
    tool_names=[],
    tool_hints="",  # Baseline has no special tool hints
    prompt_source_files_regex=None  # Baseline is ALLOWED to read source files
)

# Global registry
ARMS: dict[str, Arm] = {arm.name: arm for arm in [ARM_LINEAGELENS, ARM_CODEGRAPH, ARM_GRAPHIFY, ARM_BASELINE]}
```

#### Helper Function: Build MCP Config at Runtime

```python
def build_mcp_config(arm: Arm, clone_path: Path) -> Optional[dict]:
    """
    Render an arm's MCP server config, templating in the clone's absolute path
    (used by LineageLens's LINEAGELENS_PROJECT env var).
    
    Args:
        arm: The arm to configure
        clone_path: Absolute path to the clone (for path templating)
    
    Returns:
        The complete mcpServers config dict (ready for --mcp-config JSON), or None if arm has no MCP
    """
    if arm.mcp_server is None:
        return None
    
    config = copy.deepcopy(arm.mcp_server)
    
    # Template clone path into env if needed (LineageLens-specific pattern)
    if "env" in config:
        for key, val in config["env"].items():
            if val == "<abs_clone_path>":
                config["env"][key] = str(clone_path.absolute())
    
    return config
```

#### Unit Test: Sync Tool Names with Actual Server

```python
def test_lineagelens_tool_names_match_server():
    """Ensure ARM_LINEAGELENS.tool_names always matches the tools registered by create_mcp_server()."""
    from lineagelens.mcp_server import create_mcp_server
    
    server = create_mcp_server()
    # Assuming create_mcp_server() has a way to list registered tools (via introspection or a new method)
    registered_tools = server.list_tools()  # or similar
    registered_names = {tool.name.replace("mcp__lineagelens__", "") for tool in registered_tools}
    
    arm_names = set(ARM_LINEAGELENS.tool_names)
    assert registered_names == arm_names, f"Tool name mismatch: {registered_names} vs {arm_names}"
```

---

### 2. New File: `benchmark/common.py` — Shared Utilities

Relocate and generalize the tool-agnostic functions from `run_benchmark.py` (no logic changes):

```python
# Existing functions, moved verbatim:
def log(msg: str, level: str = "INFO", section: bool = False) -> None: ...
def run_cmd(cmd, cwd=None, check=True, capture=False, timeout=None): ...
def load_config(config_path: str) -> dict: ...
def resolve_pr(repo_url: str, pr_number: int, config: dict) -> dict: ...
def normalize_path(path: str) -> str: ...
def compute_metrics(identified: list, ground_truth: set) -> dict: ...
def parse_claude_jsonl(jsonl_data: str, scenario: str) -> dict: ...
def extract_files_from_response(response_text: str) -> list: ...

# Generalized function:
def prepare_clones(
    work_dir: str,
    repo_url: str,
    base_sha: str,
    arm_names: list[str],
    clone_dir_map: dict[str, str] = None,
    timeout: int = 900,
    shallow: bool = False
) -> dict[str, Path]:
    """
    Prepare N clones (one per arm) at the given base commit.
    
    Args:
        work_dir: Root work directory
        repo_url: Repository URL to clone
        base_sha: Base commit SHA to checkout
        arm_names: List of arm names to create clones for
        clone_dir_map: Dict mapping arm_name -> clone dir name (default: f"clone_{name}")
        timeout: Clone/checkout timeout in seconds
        shallow: If True, clone with --depth 1 (for Suite B, which doesn't need history)
    
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

# New function:
def run_arm_setup(arm: Arm, clone_path: Path, extra_env: dict = None) -> bool:
    """
    Run an arm's setup commands (init, analyze, etc.) and validate the result.
    
    Args:
        arm: The arm to set up
        clone_path: Path to the clone where setup runs
        extra_env: Extra environment variables for setup_cmds
    
    Returns:
        True if setup succeeded (or was a no-op for baseline), False otherwise
    """
    if not arm.setup_cmds:
        log(f"Arm {arm.name}: no setup required")
        return True
    
    # Optional pre-setup hook (e.g., LineageLens source_roots patching)
    if arm.pre_setup_hook:
        try:
            arm.pre_setup_hook(clone_path)
        except Exception as e:
            log(f"Pre-setup hook failed for {arm.name}: {e}", "ERROR")
            return False
    
    # Run setup commands
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if arm.setup_env:
        env.update(arm.setup_env)
    
    for cmd in arm.setup_cmds:
        try:
            run_cmd(cmd, cwd=str(clone_path), timeout=max(arm.setup_env.get("timeout", 900) * 2, 1200), env=env)
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

# New function:
def capture_tool_versions(arms: list[Arm]) -> dict[str, str]:
    """
    Capture version strings for all arms and the Claude CLI.
    
    Args:
        arms: List of arm objects to version
    
    Returns:
        Dict mapping tool_name -> version_string (or "unknown" if unavailable)
    """
    versions = {}
    
    # Claude CLI version
    try:
        stdout, _, _ = run_cmd(["claude", "--version"], capture=True, check=False)
        versions["claude"] = stdout.strip()
    except:
        versions["claude"] = "unknown"
    
    # Per-arm versions
    for arm in arms:
        if not arm.cli_binary:
            continue
        try:
            stdout, _, _ = run_cmd([arm.cli_binary, "--version"], capture=True, check=False)
            versions[arm.name] = stdout.strip()
        except:
            versions[arm.name] = "unknown"
    
    return versions

# Enhancement to existing parse_claude_jsonl:
def parse_claude_jsonl(jsonl_data: str, scenario: str) -> dict:
    """
    ... existing docstring ...
    
    Also captures cache token usage (if present in the result event):
        - cache_read_input_tokens
        - cache_creation_input_tokens
    """
    # ... existing code ...
    cache_read_tokens = 0
    cache_creation_tokens = 0
    
    for i, line in enumerate(lines):
        # ... existing parsing code ...
        
        if event_type == "result":
            cost_usd = obj.get("total_cost_usd", cost_usd)
            usage = obj.get("usage", {})
            tokens_in = usage.get("input_tokens", tokens_in)
            tokens_out = usage.get("output_tokens", tokens_out)
            cache_read_tokens = usage.get("cache_read_input_tokens", 0)
            cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            is_error = obj.get("is_error", False)
            if not response_text:
                response_text = obj.get("result", "")
    
    return {
        "cost_usd": cost_usd,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "tool_calls": tool_calls,
        "response_text": response_text,
        "is_error": is_error
    }
```

---

### 3. New File: `benchmark/prompts.py` — Registry-Driven Prompt Generation

Replace the 3 separate prompt files (`prompt_mcp.md`, `prompt_baseline.md`, `prompt.md`) with
registry-driven rendering:

```python
from pathlib import Path
from jinja2 import Template  # or use simple .format() if Jinja2 is overkill
from arms import Arm

def render_prompt(
    template_path: Path,
    arm: Arm,
    suite: str = "pr",  # "pr" or "arch"
    **task_fields
) -> str:
    """
    Load a prompt template and render it with arm-specific placeholders.
    
    Args:
        template_path: Path to the template file (e.g., prompt_pr_template.md)
        arm: The arm to tailor the prompt for
        suite: "pr" (PR-replication) or "arch" (architecture-Q&A)
        **task_fields: Task-specific fields (pr_title, pr_body, question, repo_name, etc.)
    
    Returns:
        Fully rendered prompt string
    """
    with open(template_path, 'r') as f:
        template_text = f.read()
    
    # Build arm-specific fields
    tool_only_constraint = ""
    if arm.mcp_server is not None:
        tool_only_constraint = (
            "**CRITICAL CONSTRAINT:** You must not read the source code directly, explore files "
            "manually, or use any prior knowledge of the repository structure. All decisions must be "
            "derived EXCLUSIVELY from the tool outputs."
        )
    
    pr_tool_hint = ""
    if suite == "pr" and "list_prs" in arm.tool_names:
        pr_tool_hint = (
            f"This tool set includes PR-native capabilities ({', '.join([t for t in arm.tool_names if 'pr' in t.lower()])}). "
            "Use them to analyze pull request impact directly."
        )
    
    # Build render context
    context = {
        "tool_only_constraint": tool_only_constraint,
        "tool_hints": arm.tool_hints,
        "pr_tool_hint": pr_tool_hint,
        **task_fields
    }
    
    # Render (simple .format() or Jinja2, depending on preference)
    rendered = template_text.format(**context)
    return rendered
```

---

### 4. New Files: `benchmark/prompt_pr_template.md` and `benchmark/prompt_arch_template.md`

Delete `prompt_mcp.md`, `prompt_baseline.md`, `prompt.md` and create two new templates:

#### `prompt_pr_template.md`

```markdown
# Code Change Analysis — {suite_name}

You are analyzing a GitHub pull request.

{tool_only_constraint}

## Pull Request

**Title:** {pr_title}

**Body:**

{pr_body}

## Your Task

Analyze the PR to understand its scope and impact:

1. **Understand the overall goal** from the title and description
2. **Identify affected modules** and their dependencies
3. **Understand existing patterns** in the codebase
4. **Determine what files need to change** based on the implementation

{tool_hints}

{pr_tool_hint}

## Output Format

At the end of your answer, provide a structured list of files you would need to change:

```
## Files I would change
path/to/file1.py
path/to/file2.py
libs/core/langchain_core/some_module.py
... (one relative path per line)
```

Do not include any explanation after this section—just the file paths, one per line.
```

#### `prompt_arch_template.md`

```markdown
# Architecture Analysis — {suite_name}

You are analyzing a codebase to answer an architecture question.

{tool_only_constraint}

## Repository

**Name:** {repo_name}

**Question:** {question}

## Your Task

Use the available tools to understand the codebase and answer the question thoroughly:

1. **Explore the relevant code** using the tool capabilities
2. **Trace call chains** and understand the flow
3. **Identify key components** and their roles
4. **Provide a clear explanation** of how the system works

{tool_hints}

## Output Format

Provide a clear, structured explanation of the architecture in response to the question above.
Include code references, call flows, and the key concepts involved.
```

---

### 5. Rewrite File: `benchmark/run_pr_benchmark.py` (replaces `run_benchmark.py`)

Same pipeline as today (resolve PR → clone → setup → ground truth → Claude scenarios → scoring
→ report), generalized to N arms:

```python
def main():
    parser = argparse.ArgumentParser(description="LineageLens PR-replication benchmark (multi-arm)")
    parser.add_argument("--config", type=str, default="benchmark/benchmark.yaml", help="Config file path")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts and show commands without running Claude")
    args = parser.parse_args()
    
    config = load_config(args.config)
    arms_list = [ARMS[name] for name in config.get("arms", ["lineagelens", "baseline"])]
    
    # Resolve PR metadata
    pr_info = resolve_pr(config["repo"]["url"], config["repo"]["pr"], config)
    
    # Prepare clones (one per arm)
    clone_dir_map = config.get("clone_dirs", {})
    clones = prepare_clones(
        config["work_dir"],
        config["repo"]["url"],
        pr_info["base_sha"],
        [arm.name for arm in arms_list],
        clone_dir_map,
        timeout=config.get("timeout_seconds", 900),
        shallow=False  # PR suite needs git diff history
    )
    
    # Setup each arm
    for arm in arms_list:
        success = run_arm_setup(arm, clones[arm.name], config)
        if not success:
            log(f"Setup failed for arm {arm.name}", "ERROR")
            sys.exit(1)
    
    # Get ground truth (files changed in PR)
    ground_truth = get_ground_truth_files(
        clones[arms_list[0].name],  # Use any clone; all are at base_sha
        pr_info["base_sha"],
        pr_info["merge_sha"]
    )
    
    # Load prompts
    prompts = {}
    for arm in arms_list:
        prompt_path = Path(__file__).parent / "prompt_pr_template.md"
        prompts[arm.name] = render_prompt(
            prompt_path,
            arm,
            suite="pr",
            pr_title=pr_info["title"],
            pr_body=pr_info["body"],
            suite_name="Scenario: " + arm.name
        )
    
    if args.dry_run:
        # Show rendered prompts and commands without executing
        for arm in arms_list:
            log(f"\n{'='*80}", "INFO")
            log(f"Arm: {arm.name}", "INFO")
            log(f"{'='*80}", "INFO")
            log(f"Prompt (first 500 chars):\n{prompts[arm.name][:500]}\n...", "INFO")
            
            mcp_config = build_mcp_config(arm, clones[arm.name])
            cmd = build_claude_command(arm, prompts[arm.name], clones[arm.name], config, mcp_config)
            log(f"Claude command:\n{' '.join(cmd)}\n", "INFO")
        
        log("Dry-run complete; no clones or API calls made.", "INFO")
        return
    
    # Run Claude scenarios
    results_dir = Path(config["work_dir"]) / "results"
    results = []
    
    for arm in arms_list:
        log(f"Running Claude for arm: {arm.name}", "INFO", section=True)
        result = run_claude_scenario(
            arm,
            prompts[arm.name],
            clones[arm.name],
            config,
            results_dir
        )
        if not result:
            log(f"FAILED: arm {arm.name} did not complete", "ERROR")
            sys.exit(1)
        results.append(result)
    
    # Capture tool versions for reproducibility
    versions = capture_tool_versions(arms_list)
    
    # Generate report
    versions_json = results_dir / "versions.json"
    with open(versions_json, 'w') as f:
        json.dump(versions, f, indent=2)
    
    write_pr_report(results, arms_list, ground_truth, config["work_dir"], config["repo"]["pr"], versions)
    
    log("Benchmark COMPLETE", "INFO", section=True)

def build_claude_command(arm: Arm, prompt: str, clone_path: Path, config: dict, mcp_config: dict = None) -> list:
    """Build the exact 'claude -p ...' command for an arm."""
    cmd = ["claude", "-p", prompt]
    
    if mcp_config:
        cmd.extend([
            "--mcp-config", json.dumps(mcp_config),
            "--strict-mcp-config",
            "--allowedTools", arm.allowed_tools
        ])
        if arm.disallowed_tools:
            cmd.extend(["--disallowedTools", arm.disallowed_tools])
    else:
        cmd.extend(["--allowedTools", arm.allowed_tools])
    
    cmd.extend([
        "--model", config.get("model", "claude-sonnet-4-5"),
        "--max-budget-usd", str(config.get("budget_usd", 3.00)),
        "--output-format", "stream-json",
        "--verbose"
    ])
    
    return cmd

def run_claude_scenario(arm: Arm, prompt: str, clone_path: Path, config: dict, results_dir: Path) -> dict:
    """
    Run Claude with an arm's tool configuration.
    
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
        result = subprocess.run(cmd, cwd=str(clone_path), capture_output=True, text=True, timeout=config.get("timeout_seconds", 900))
        duration = time.time() - start_time
        
        # Persist transcript
        results_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = results_dir / f"{arm.name}_stream.jsonl"
        with open(transcript_path, 'w') as f:
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
            "raw_response": parsed.get("response_text", "")
        }
    
    except subprocess.TimeoutExpired:
        log(f"Claude call timed out", "ERROR")
        return {}
    except Exception as e:
        log(f"Claude call failed: {e}", "ERROR")
        return {}

if __name__ == "__main__":
    main()
```

---

### 6. New File: `benchmark/run_arch_benchmark.py` — Suite B

Modeled on CodeGraph's own published methodology:

```python
def main():
    parser = argparse.ArgumentParser(description="Architecture-Q&A efficiency benchmark (multi-arm)")
    parser.add_argument("--config", type=str, default="benchmark/arch_benchmark.yaml", help="Config file path")
    args = parser.parse_args()
    
    config = load_config(args.config)
    arms_list = [ARMS[name] for name in config.get("arms", ["lineagelens", "baseline"])]
    
    all_aggregated = {}
    
    # Per repo
    for repo_cfg in config.get("repos", []):
        log(f"Benchmarking repo: {repo_cfg['url']}", "INFO", section=True)
        
        # Prepare clones (one per arm, shallow since we don't need git history)
        clones = prepare_clones(
            config["work_dir"],
            repo_cfg["url"],
            "HEAD",  # For architecture Q&A, just use HEAD (no PR base/merge distinction)
            [arm.name for arm in arms_list],
            shallow=True
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
            log(f"Running {config.get('runs_per_arm', 4)} runs for arm: {arm.name}", "INFO")
            
            prompt = render_prompt(
                prompt_template_path,
                arm,
                suite="arch",
                repo_name=repo_cfg.get("name", repo_cfg["url"]),
                question=repo_cfg["question"],
                suite_name="Scenario: " + arm.name
            )
            
            runs = []
            for run_idx in range(config.get("runs_per_arm", 4)):
                # Set up contamination-blocking environment (arch suite only)
                exclude_binaries = [a.cli_binary for a in arms_list if a.cli_binary and a.name != arm.name]
                sanitized_path = build_sanitized_path(exclude_binaries)
                
                # Run Claude with contamination guards
                env_overrides = {"PATH": sanitized_path}
                result = run_claude_scenario_with_contamination_check(
                    arm,
                    prompt,
                    clones[arm.name],
                    config,
                    env_overrides,
                    repo_cfg["url"]
                )
                
                if result:
                    runs.append(result)
                    log(f"  Run {run_idx + 1} complete: {result.get('tool_calls', 0)} tool calls, "
                        f"{result.get('duration_sec', 0):.1f}s, {result.get('cost_usd', 0):.2f} USD", "INFO")
            
            results_by_arm[arm.name] = runs
        
        # Aggregate medians
        aggregated = aggregate_medians(results_by_arm, repo_cfg)
        all_aggregated[repo_cfg["url"]] = aggregated
    
    # Capture versions
    versions = capture_tool_versions(arms_list)
    
    # Generate report
    write_arch_report(all_aggregated, arms_list, config["work_dir"], versions)
    
    log("Architecture benchmark COMPLETE", "INFO", section=True)

def aggregate_medians(results_by_arm: dict[str, list[dict]], repo_cfg: dict) -> dict:
    """Compute medians across N runs per arm."""
    aggregated = {"repo": repo_cfg["url"], "question": repo_cfg["question"], "arms": {}}
    
    for arm_name, runs in results_by_arm.items():
        if not runs:
            aggregated["arms"][arm_name] = {"status": "no_data"}
            continue
        
        metrics = {
            "tool_calls": [r.get("tool_calls_count", 0) for r in runs],
            "duration_sec": [r.get("duration_sec", 0) for r in runs],
            "file_reads": [r.get("file_reads_count", 0) for r in runs],
            "tokens": [r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in runs],
            "cost_usd": [r.get("cost_usd", 0) for r in runs]
        }
        
        aggregated["arms"][arm_name] = {
            "median_tool_calls": statistics.median(metrics["tool_calls"]),
            "median_duration_sec": statistics.median(metrics["duration_sec"]),
            "median_file_reads": statistics.median(metrics["file_reads"]),
            "median_tokens": statistics.median(metrics["tokens"]),
            "median_cost_usd": statistics.median(metrics["cost_usd"]),
            "runs_count": len(runs)
        }
    
    return aggregated
```

---

### 7. New File: `benchmark/contamination.py` — Anti-Contamination Layer (Suite B Only)

Adapted from CodeGraph's own design:

```python
def build_sanitized_path(exclude_binaries: list[str]) -> str:
    """
    Create a temporary directory with symlinks to all executables on $PATH,
    except those in exclude_binaries. This ensures tools can only be reached
    via MCP, not via Bash.
    
    Args:
        exclude_binaries: List of binary names to exclude (e.g., ["codegraph", "graphify"])
    
    Returns:
        Path to the sanitized temp directory (prepend to subprocess PATH)
    """
    temp_dir = tempfile.mkdtemp(prefix="ll-bench-path-")
    
    # Find all binaries on the real PATH
    real_path = os.environ.get("PATH", "").split(os.pathsep)
    
    for path_dir in real_path:
        if not os.path.isdir(path_dir):
            continue
        
        for entry in os.listdir(path_dir):
            entry_path = os.path.join(path_dir, entry)
            
            # Skip directories and non-executables
            if not os.path.isfile(entry_path) or not os.access(entry_path, os.X_OK):
                continue
            
            # Skip excluded binaries
            if entry in exclude_binaries:
                continue
            
            # Create symlink in temp dir
            symlink_path = os.path.join(temp_dir, entry)
            if not os.path.exists(symlink_path):  # Avoid duplicates if a binary is in multiple PATH dirs
                os.symlink(entry_path, symlink_path)
    
    return temp_dir

def write_pretool_hook(exclude_binaries: list[str], hooks_dir: Path) -> Path:
    """
    Write a PreToolUse hook script that blocks Bash commands naming excluded binaries.
    
    Args:
        exclude_binaries: List of binary names to block
        hooks_dir: Directory to write the hook script to
    
    Returns:
        Path to the generated hook script
    """
    hook_path = hooks_dir / "benchmark_contamination_check.sh"
    
    # Build a regex pattern that matches any excluded binary name
    pattern = "|".join(re.escape(b) for b in exclude_binaries)
    
    hook_script = f"""#!/bin/bash
# PreToolUse hook: block Bash commands that invoke excluded binaries
# Generated by benchmark/contamination.py

COMMAND="$1"

if echo "$COMMAND" | grep -qE '(^|\\s|/|;)({pattern})'; then
    echo "BLOCKED: Benchmark contamination detected. Command references excluded binary: $COMMAND" >&2
    exit 1
fi

# If not blocked, allow the tool call to proceed
exit 0
"""
    
    with open(hook_path, 'w') as f:
        f.write(hook_script)
    
    os.chmod(hook_path, 0o755)
    return hook_path

def parse_run_for_contamination(transcript_path: Path, exclude_binaries: list[str]) -> list[str]:
    """
    Post-hoc scan of a JSONL transcript for Bash commands invoking excluded binaries.
    
    Args:
        transcript_path: Path to the stream-json JSONL file
        exclude_binaries: List of binary names to look for
    
    Returns:
        List of violation strings (empty if none found)
    """
    violations = []
    pattern = "|".join(re.escape(b) for b in exclude_binaries)
    
    try:
        with open(transcript_path, 'r') as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Look for tool_use events in assistant blocks
                if obj.get("type") == "assistant":
                    for block in obj.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use" and block.get("name") == "Bash":
                            cmd = block.get("input", {}).get("command", "")
                            if re.search(f'(^|\\s|/|;)({pattern})', cmd):
                                violations.append(f"Line {line_no}: Bash command invokes excluded binary: {cmd}")
    
    except Exception as e:
        log(f"Error scanning transcript: {e}", "WARN")
    
    return violations
```

---

### 8. New File: `benchmark/report.py` — Generalized Reporting

```python
def write_pr_report(
    results: list[dict],
    arms: list[Arm],
    ground_truth: set,
    work_dir: str,
    pr_num: int,
    versions: dict = None
) -> tuple[Path, Path]:
    """
    Generate summary.md and summary.json for PR-replication suite (N-arm variant).
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
    with open(summary_md_path, 'w') as f:
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
        f.write("| Precision | " + " | ".join([f"{metrics_by_arm[n]['precision']:.3f}" for n in arm_names]) + " |\n")
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
            f.write(f"- Precision: {metrics['precision']:.3f}, Recall: {metrics['recall']:.3f}, F1: {metrics['f1']:.3f}\n")
            
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
        "arms": {}
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
            "files_identified": result.get("files_identified", [])
        }
    
    with open(summary_json_path, 'w') as f:
        json.dump(summary_data, f, indent=2)
    
    log(f"Report written to {summary_md_path}", "INFO")
    return (summary_md_path, summary_json_path)

def write_arch_report(
    aggregated: dict[str, dict],
    arms: list[Arm],
    work_dir: str,
    versions: dict = None
) -> tuple[Path, Path]:
    """
    Generate summary.md and summary.json for architecture-Q&A suite.
    Tables are formatted: repos × metrics, columns = arms, cells = medians.
    """
    work_path = Path(work_dir)
    results_dir = work_path / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    summary_md_path = results_dir / "summary.md"
    with open(summary_md_path, 'w') as f:
        f.write("# Architecture-Q&A Benchmark Results\n\n")
        f.write("Median values across multiple runs per arm and repo.\n\n")
        
        arm_names = [arm.name for arm in arms]
        baseline_median = None
        
        # Per-metric table
        metrics = ["median_tool_calls", "median_duration_sec", "median_tokens", "median_cost_usd"]
        
        for metric in metrics:
            f.write(f"\n## {metric}\n\n")
            f.write("| Repo | " + " | ".join(arm_names) + " |\n")
            f.write("|------|" + "|".join(["---" for _ in arm_names]) + "|\n")
            
            baseline_repo_values = []
            
            for repo_url, repo_data in aggregated.items():
                f.write(f"| {repo_url.split('/')[-1]} ")
                
                for arm_name in arm_names:
                    arm_data = repo_data.get("arms", {}).get(arm_name, {})
                    value = arm_data.get(metric, "N/A")
                    
                    if arm_name == "baseline" and isinstance(value, (int, float)):
                        baseline_repo_values.append(value)
                    
                    f.write(f"| {value:.2f} " if isinstance(value, float) else f"| {value} ")
                
                f.write("|\n")
            
            # Reduction percentages vs baseline
            f.write("\n### % Reduction vs Baseline\n\n")
            f.write("| Repo | " + " | ".join([a for a in arm_names if a != "baseline"]) + " |\n")
            f.write("|------|" + "|".join(["---" for _ in arm_names if _ != "baseline"]) + "|\n")
            
            for repo_url, repo_data in aggregated.items():
                baseline_val = repo_data.get("arms", {}).get("baseline", {}).get(metric)
                if baseline_val is None or baseline_val == 0:
                    continue
                
                f.write(f"| {repo_url.split('/')[-1]} ")
                
                for arm_name in arm_names:
                    if arm_name == "baseline":
                        continue
                    
                    arm_val = repo_data.get("arms", {}).get(arm_name, {}).get(metric)
                    if arm_val is not None:
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
    with open(summary_json_path, 'w') as f:
        json.dump({
            "aggregated": aggregated,
            "versions": versions or {}
        }, f, indent=2)
    
    log(f"Report written to {summary_md_path}", "INFO")
    return (summary_md_path, summary_json_path)
```

---

### 9. Update `benchmark/benchmark.yaml`

Add an `arms` key and generalize clone directories:

```yaml
repo:
  url: https://github.com/langchain-ai/langchain
  pr: 39809
  base_commit: ""
  expected_commit: ""

language: python
arms: [lineagelens, codegraph, graphify, baseline]

work_dir: /tmp/ll-bench

# NEW: Generalized clone directory mapping (optional; defaults to clone_{arm_name})
clone_dirs:
  lineagelens: mcptest
  codegraph: cgtest
  graphify: gftest
  baseline: nonmcp_test

source_roots: []
model: claude-sonnet-4-5
budget_usd: 3.00
timeout_seconds: 900
mcp_tool_prefix: mcp__lineagelens
```

Add a back-compat shim in `common.py`'s `load_config` to map old `mcp_dir`/`nonmcp_dir` keys if
present.

---

### 10. New File: `benchmark/arch_benchmark.yaml`

Config for the architecture-Q&A suite:

```yaml
arms: [lineagelens, codegraph, graphify, baseline]

runs_per_arm: 4

model: claude-sonnet-4-5
budget_usd: 3.00
timeout_seconds: 900

work_dir: /tmp/ll-arch-bench

repos:
  - url: https://github.com/apache/dubbo
    name: "Apache Dubbo"
    question: "How does Dubbo's service registration flow from provider startup to registry write?"
    language: java
    source_roots: []
  
  - url: https://github.com/langchain-ai/langchain
    name: "LangChain"
    question: "How does LangChain compose multiple chains and manage state across them?"
    language: python
    source_roots: ["libs/langchain/langchain"]
```

---

### 11. Delete Old Files

- `benchmark/run_benchmark.py` (replaced by `run_pr_benchmark.py`)
- `benchmark/prompt_mcp.md` (replaced by `prompt_pr_template.md`)
- `benchmark/prompt_baseline.md` (merged into `prompt_pr_template.md`)
- `benchmark/prompt.md` (replaced by `prompt_pr_template.md`)

---

### 12. Update `benchmark/README.md`

Document:
- The two suites (PR-replication and architecture-Q&A)
- How to add a new arm (register in `arms.py`, done)
- Usage: `python benchmark/run_pr_benchmark.py --config benchmark.yaml` and `python
  benchmark/run_arch_benchmark.py --config arch_benchmark.yaml`
- The `--dry-run` flag for free validation
- The verification sequence (dry-run → smoke test → langchain → full Dubbo/arch runs)

---

### 13. No Changes to `src/lineagelens/mcp_server.py` or `pyproject.toml`

The only place that needs to stay in sync is `arms.py`'s `ARM_LINEAGELENS.tool_names` list, enforced by
the unit test (see section 1).

---

## Verification Plan

The spec includes a detailed verification sequence (free dry-run → tiny smoke-test → langchain PR
#39809 → per-arm setup isolation → contamination self-test → full runs) to be followed by the
implementing agent before spending real budget on Dubbo/multi-repo runs.

---

## Summary

This refactor transforms a 2-arm hardcoded harness into a pluggable, configuration-driven,
reproducible multi-tool benchmark suite. The centerpiece is the `Arm` registry in `arms.py` —
add LineageLens vs. CodeGraph vs. Graphify vs. baseline support without touching the runner
logic, fix the tool-name bug at the root by deriving prompts from the registry, and capture
tool versions for reproducibility.
