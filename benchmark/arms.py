"""Pluggable arm registry for multi-tool benchmark suite.

Each 'arm' represents a code-graph tool or baseline that can be benchmarked.
Defines tool-specific setup, MCP configuration, validation, and prompt hints.
"""

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class Arm:
    """Describes a code-graph tool (or baseline) as a benchmark 'arm' (contestant)."""

    # Identity
    name: str  # "lineagelens" | "codegraph" | "graphify" | "baseline"

    # CLI binary name for anti-contamination (arch suite only)
    cli_binary: Optional[str] = None  # e.g. "lineagelens", "codegraph", "graphify"; None for baseline

    # Setup phase: commands to run once per clone
    setup_cmds: list[list[str]] = field(
        default_factory=list
    )  # e.g. [["lineagelens","init","."], ["lineagelens","analyze",".","--quiet"]]
    setup_env: dict[str, str] = field(default_factory=dict)  # extra env vars for setup_cmds

    # Pre-setup hook: arm-specific customization (e.g., LineageLens source_roots patching)
    pre_setup_hook: Optional[Callable[[Path], None]] = None

    # Validation: guard against misconfiguration (e.g., empty graph)
    validate_setup: Optional[Callable[[Path], bool]] = None

    # MCP configuration (None for baseline, which uses no MCP)
    mcp_server: Optional[dict] = None  # e.g. {"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"<path>"}}

    # Tool access restrictions
    allowed_tools: str = ""  # e.g. "mcp__lineagelens__*" or "Read,Glob,Grep,Bash(find *),Bash(ls *)"
    disallowed_tools: Optional[str] = None  # e.g. "mcp__lineagelens__trigger_analysis"; None = no restrictions

    # Extra env vars to set when invoking Claude with this arm's tools
    extra_env: dict[str, str] = field(default_factory=dict)  # e.g. {"CODEGRAPH_MCP_TOOLS": "1"}

    # Tool names, for documentation and prompt generation
    tool_names: list[str] = field(default_factory=list)  # e.g. ["get_symbol", "search_symbols", "get_callers", ...]

    # Natural-language usage hint block, rendered into prompt templates
    tool_hints: str = ""  # e.g. "Use search_symbols to find a symbol...", empty for baseline

    # Regex for detecting raw-source-file reads (MCP-only arms); None disables the check
    prompt_source_files_regex: Optional[str] = r"\.(py|java|ts|tsx|js|jsx)$"


# Validation helpers
def validate_lineagelens_graph(clone_path: Path) -> bool:
    """Check that LineageLens graph.json has >= 20 symbols (guards against empty/misconfigured graph)."""
    import json

    graph_file = clone_path / ".lineagelens" / "graph.json"
    if not graph_file.exists():
        return False

    try:
        with open(graph_file, "r") as f:
            graph = json.load(f)
        symbol_count = len(graph.get("symbols", []))
        return symbol_count >= 20
    except Exception:
        return False


def validate_codegraph_index(clone_path: Path) -> bool:
    """Check that CodeGraph .codegraph/ directory exists and is non-empty."""
    codegraph_dir = clone_path / ".codegraph"
    return codegraph_dir.exists() and len(list(codegraph_dir.iterdir())) > 0


def validate_graphify_graph(clone_path: Path) -> bool:
    """Check that Graphify graph.json exists and has content."""
    import json

    graph_file = clone_path / "graphify-out" / "graph.json"
    if not graph_file.exists():
        return False

    try:
        with open(graph_file, "r") as f:
            graph = json.load(f)
        nodes = graph.get("nodes", [])
        return len(nodes) > 0
    except Exception:
        return False


def patch_lineagelens_source_roots(clone_path: Path) -> None:
    """Pre-setup hook: patch lineagelens.yaml with source_roots if provided in config.
    This is called by run_arm_setup() if the arm has a pre_setup_hook, and receives
    the clone path. The actual source_roots value is passed via the config, not here.
    For now, this is a no-op placeholder; the real patching happens in run_pr_benchmark.py
    before run_arm_setup() is called."""
    pass


# Concrete Arm Registrations

ARM_LINEAGELENS = Arm(
    name="lineagelens",
    cli_binary="lineagelens",
    setup_cmds=[
        ["lineagelens", "init", "."],
        ["lineagelens", "analyze", ".", "--quiet"],
    ],
    pre_setup_hook=patch_lineagelens_source_roots,
    validate_setup=validate_lineagelens_graph,
    mcp_server={
        "command": "lineagelens-mcp",
        "env": {"LINEAGELENS_PROJECT": "<abs_clone_path>"},
    },
    allowed_tools="mcp__lineagelens__*",
    disallowed_tools="mcp__lineagelens__trigger_analysis",
    tool_names=[
        "get_symbol",
        "search_symbols",
        "get_callers",
        "get_callees",
        "get_lineage",
        "get_impact",
        "list_implementations",
        "list_providers",
        "get_module_overview",
        "list_entry_points",
        "list_risks",
        "get_project_config",
        "get_metrics",
        "find_entry_points",
        "get_module_dependencies",
        "list_dead_code",
        "get_reachability",
        "list_duplicate_names",
        "trigger_analysis",
        "list_fields_by_type",
    ],
    tool_hints="""Use search_symbols to find relevant classes/functions. Use get_callers and get_callees
to trace dependencies. Use get_lineage to understand inheritance chains. Use get_impact to see
what code changes would affect. Use get_module_overview to understand a module's entry points.""",
    prompt_source_files_regex=r"\.(py|java|ts|tsx|js|jsx)$",
)

ARM_CODEGRAPH = Arm(
    name="codegraph",
    cli_binary="codegraph",
    setup_cmds=[["codegraph", "init"]],
    validate_setup=validate_codegraph_index,
    mcp_server={
        "type": "stdio",
        "command": "codegraph",
        "args": ["serve", "--mcp"],
    },
    extra_env={"CODEGRAPH_MCP_TOOLS": "1"},
    allowed_tools="mcp__codegraph__*",
    tool_names=[
        "codegraph_explore",
        "codegraph_node",
        "codegraph_search",
        "codegraph_callers",
        "codegraph_callees",
        "codegraph_impact",
        "codegraph_files",
        "codegraph_status",
    ],
    tool_hints="""Use codegraph_explore to answer 'how does X work' and 'how does X reach Y' questions.
Use codegraph_impact to see the blast radius of changes. Use codegraph_callers and codegraph_callees
to trace call chains. codegraph_explore is the main tool; others provide lower-level detail.""",
    prompt_source_files_regex=r"\.(ts|tsx|js|jsx|py|java)$",
)

ARM_GRAPHIFY = Arm(
    name="graphify",
    cli_binary="graphify",
    setup_cmds=[["graphify", "extract", ".", "--code-only"]],
    validate_setup=validate_graphify_graph,
    mcp_server={
        "command": "python",
        "args": ["-m", "graphify.serve", "graphify-out/graph.json"],
    },
    allowed_tools="mcp__graphify__*",
    tool_names=[
        "query_graph",
        "get_node",
        "get_neighbors",
        "shortest_path",
        "list_prs",
        "get_pr_impact",
        "triage_prs",
    ],
    tool_hints="""Use query_graph to find relevant symbols and relationships. Use get_node to inspect
a single symbol. Use shortest_path to understand how two concepts connect. For PR analysis, use
list_prs to enumerate pull requests, get_pr_impact to see which files a PR changes, and triage_prs
to assess PR scope.""",
    prompt_source_files_regex=r"\.(py|java|ts|tsx|js|jsx)$",
)

ARM_BASELINE = Arm(
    name="baseline",
    cli_binary=None,
    setup_cmds=[],
    mcp_server=None,
    allowed_tools="Read,Glob,Grep,Bash(find *),Bash(ls *)",
    tool_names=[],
    tool_hints="",
    prompt_source_files_regex=None,
)

# Global registry
ARMS: dict[str, Arm] = {arm.name: arm for arm in [ARM_LINEAGELENS, ARM_CODEGRAPH, ARM_GRAPHIFY, ARM_BASELINE]}


def build_mcp_config(arm: Arm, clone_path: Path) -> Optional[dict]:
    """Render an arm's MCP server config, templating in the clone's absolute path.

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
