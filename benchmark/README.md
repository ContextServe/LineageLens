# Multi-Tool Benchmark Suite: LineageLens vs. CodeGraph vs. Graphify

A reusable, reproducible, configuration-driven benchmark suite for comparing code-graph tools.

## Overview

Two integrated benchmark suites:

1. **Suite A: PR-Replication** (`run_pr_benchmark.py`) — Clones a real GitHub PR at its base commit, gives Claude the PR title/body (never the diff), and scores the "files I would change" predictions against the hidden diff ground truth. Supports N arms (tools). Scores by F1 (precision/recall).

2. **Suite B: Architecture-Q&A** (`run_arch_benchmark.py`) — Modeled on CodeGraph's own published methodology. Asks a fixed architecture question per repo, runs N times per arm, measures efficiency (tool calls, tokens, cost, wall-clock time). Includes anti-contamination layer (sanitized PATH + hooks) to ensure tools are only reached via MCP.

Both suites share:
- **Pluggable arm registry** (`arms.py`) — add a new tool as one registry entry, no code changes
- **Shared utilities** (`common.py`) — cloning, setup, validation, JSONL parsing, F1 scoring, version capture
- **Registry-driven prompts** (`prompts.py` + templates) — tool-specific hints rendered from the registry, no hand-typed prose
- **Generalized reporting** (`report.py`) — N-column tables for both suites

## Quick Start (Automated)

### Running the Complete Benchmark Suite

```bash
# Prerequisites
pip install lineagelens
npm i -g @colbymchenry/codegraph      # for CodeGraph (optional)
uv tool install graphifyy              # for Graphify (optional)

# Run Suite A: PR-Replication Benchmark
python benchmark/run_pr_benchmark.py --config benchmark/benchmark.yaml

# Results at: /tmp/ll-bench/results/summary.md
cat /tmp/ll-bench/results/summary.md
```

---

## Manual Instructions (Step-by-Step)

### Suite A: PR-Replication Benchmark (Manual)

Compare LineageLens vs. Baseline on LangChain PR #39809:

```bash
# 1. Clone and checkout PR base commit
WORK_DIR="/tmp/manual-bench"
mkdir -p $WORK_DIR
cd $WORK_DIR

git clone https://github.com/langchain-ai/langchain ll_clone
cd ll_clone
git checkout a2024abe50a1db8dba3884a2a45c91989e6b561e  # PR base commit

# 2. Build LineageLens graph
lineagelens init .
# Edit lineagelens.yaml to set source_roots:
sed -i '' 's/source_roots: \[\]/source_roots: ["libs\/langchain\/langchain_classic"]/' lineagelens.yaml
lineagelens analyze . --quiet

# Verify graph was built
ls -lh .lineagelens/graph.json
python3 -c "import json; g=json.load(open('.lineagelens/graph.json')); print(f'Symbols: {len(g.get(\"symbols\", []))}')"

# 3. Test LineageLens MCP tools
cd $WORK_DIR/ll_clone
claude -p "Using the available code graph tools, list the main frameworks used in libs/langchain/langchain_classic/" \
  --mcp-config '{"mcpServers":{"lineagelens":{"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"'$WORK_DIR'/ll_clone"}}}}' \
  --strict-mcp-config \
  --allowedTools "mcp__lineagelens__*" \
  --disallowedTools "mcp__lineagelens__trigger_analysis" \
  --model claude-sonnet-4-5 \
  --max-budget-usd 1.0

# 4. Test baseline tools (file exploration)
claude -p "Using Read, Glob, and Grep tools only, identify which files in libs/langchain/langchain_classic define chat models." \
  --allowedTools "Read,Glob,Grep,Bash(find *)" \
  --model claude-sonnet-4-5 \
  --max-budget-usd 1.0
```

### Suite B: Architecture-Q&A (Manual)

Analyze Apache Dubbo architecture:

```bash
# 1. Clone and checkout Dubbo
WORK_DIR="/tmp/manual-arch-bench"
mkdir -p $WORK_DIR
cd $WORK_DIR

git clone https://github.com/apache/dubbo dubbo_clone
cd dubbo_clone
git checkout HEAD

# 2. Build LineageLens graph for Dubbo
lineagelens init .
# Configure for Java (if needed, edit lineagelens.yaml)
lineagelens analyze . --quiet

# Verify graph
python3 -c "import json; g=json.load(open('.lineagelens/graph.json')); print(f'Symbols: {len(g.get(\"symbols\", []))}')"

# 3. Ask architecture question with MCP tools
cd $WORK_DIR/dubbo_clone
claude -p "How does Dubbo's service registration flow from provider startup to registry write? Trace the complete call chain." \
  --mcp-config '{"mcpServers":{"lineagelens":{"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"'$WORK_DIR'/dubbo_clone"}}}}' \
  --strict-mcp-config \
  --allowedTools "mcp__lineagelens__*" \
  --disallowedTools "mcp__lineagelens__trigger_analysis" \
  --model claude-sonnet-4-5 \
  --max-budget-usd 2.0

# 4. Same question with baseline tools
claude -p "How does Dubbo's service registration flow from provider startup to registry write? Use Read, Glob, Grep to trace it." \
  --allowedTools "Read,Glob,Grep,Bash(find *)" \
  --model claude-sonnet-4-5 \
  --max-budget-usd 2.0
```

### Comparing Tools: CodeGraph

```bash
# 1. Install CodeGraph
npm i -g @colbymchenry/codegraph

# 2. Setup CodeGraph on LangChain
WORK_DIR="/tmp/manual-bench"
cd $WORK_DIR/ll_clone
codegraph init
# This builds .codegraph/ index

# 3. Test CodeGraph MCP tools
cd $WORK_DIR/ll_clone
claude -p "Using the available code graph, identify which files import ChatOpenAI." \
  --mcp-config '{"mcpServers":{"codegraph":{"type":"stdio","command":"codegraph","args":["serve","--mcp"]}}}' \
  --strict-mcp-config \
  --allowedTools "mcp__codegraph__*" \
  --model claude-sonnet-4-5 \
  --max-budget-usd 1.0
```

### Comparing Tools: Graphify

```bash
# 1. Install Graphify
uv tool install graphifyy

# 2. Build Graphify graph on LangChain
WORK_DIR="/tmp/manual-bench"
cd $WORK_DIR/ll_clone
graphify extract . --code-only
# This builds graphify-out/graph.json

# 3. Start Graphify MCP server
python -m graphify.serve $WORK_DIR/ll_clone/graphify-out/graph.json &

# 4. Test Graphify MCP tools
claude -p "Using the code graph, which files define chat model classes?" \
  --mcp-config '{"mcpServers":{"graphify":{"command":"python","args":["-m","graphify.serve","'$WORK_DIR'/ll_clone/graphify-out/graph.json"]}}}' \
  --strict-mcp-config \
  --allowedTools "mcp__graphify__*" \
  --model claude-sonnet-4-5 \
  --max-budget-usd 1.0

# 5. Stop server
pkill -f "graphify.serve"
```

---

## Interpreting Results

After running any test, look at Claude's response for:

1. **Tool Calls Made** — How many times did Claude invoke MCP tools vs. file tools?
   - MCP tools: should be dozens for complex questions
   - File tools (Read/Glob/Grep): baseline approach

2. **Token Usage** — Input/output token count (visible in Claude output)
   - MCP approach should use fewer tokens overall
   - File exploration uses more tokens for raw source

3. **Cost** — Shown in Claude output (tokens × model rate)
   - Compare cost between tools

4. **Quality** — Did Claude identify the correct files/concepts?
   - Check against what you know about the codebase

## Configuration

### Suite A (`benchmark.yaml`)

```yaml
repo:
  url: https://github.com/langchain-ai/langchain
  pr: 39809

language: python                              # python | java | js
arms: [lineagelens, codegraph, graphify, baseline]  # which tools to benchmark

work_dir: /tmp/ll-bench

clone_dirs:                                   # optional; defaults to clone_{arm_name}
  lineagelens: mcptest
  codegraph: cgtest
  graphify: gftest
  baseline: nonmcp_test

source_roots: []                              # for non-src/ layouts (e.g., ["libs/langchain/langchain"])

model: claude-sonnet-4-5
budget_usd: 3.00
timeout_seconds: 900
```

### Suite B (`arch_benchmark.yaml`)

```yaml
arms: [lineagelens, codegraph, graphify, baseline]
runs_per_arm: 4                               # medians reported

model: claude-sonnet-4-5
budget_usd: 3.00
timeout_seconds: 900

work_dir: /tmp/ll-arch-bench

repos:
  - url: https://github.com/apache/dubbo
    name: "Apache Dubbo"
    question: "How does Dubbo's service registration flow...?"
    language: java
    source_roots: []
```

## Arm Registry (`arms.py`)

Adding a new tool is one registry entry:

```python
ARM_MYTOOL = Arm(
    name="mytool",
    cli_binary="mytool",
    setup_cmds=[["mytool", "init", "."], ["mytool", "index", "."]],
    validate_setup=validate_mytool_index,
    mcp_server={"command": "mytool-mcp"},
    allowed_tools="mcp__mytool__*",
    tool_names=["tool_a", "tool_b", ...],
    tool_hints="Use tool_a to..., tool_b to...",
)

# Then register it:
ARMS["mytool"] = ARM_MYTOOL
```

## Reproducibility

All reports include captured tool versions:
- `lineagelens --version`
- `codegraph --version`
- `graphify --version`
- `claude --version`

These are stored in `summary.json` under `versions` key, so re-runs months later can show why numbers changed.

## Verification Sequence

Before spending real budget on Dubbo/multi-repo runs:

1. **Free dry-run**: `run_pr_benchmark.py --dry-run` — validates config, arm lookups, rendered prompts, exact Claude commands
2. **Tiny smoke-test repo/PR** — throwaway config with 2-3 file PR, exercises real `codegraph init`/`graphify extract`/`lineagelens analyze` and N Claude calls for cents
3. **Langchain PR #39809** — already configured in `benchmark.yaml`, exercises `source_roots` patching
4. **Per-arm setup isolation** — run `run_arm_setup()` per arm against smoke repo to isolate tool CLI issues from MCP wiring
5. **Contamination self-test** (Suite B) — deliberately leaky prompt asking Claude to shell out to a blocked binary, confirm hook rejects it
6. **Full runs** — Suite A (all arms), Suite B (multi-repo) with `runs_per_arm: 1` first, then scale to `runs_per_arm: 4`

## Known Limitations

- **Suite A** scoring is file-level, not symbol-level (PR tool lists, not API modifications)
- **Suite B** graphs don't include design-doc rationale edges (only code)
- **All suites** LLM results are non-deterministic; multiple runs show convergence trends, not bit-identical reproducibility

## Tool-Name Bug (Now Fixed)

Prior versions of `prompt_mcp.md` referenced non-existent tool names (`query_code_graph`, `get_symbol_references`, `get_symbol_callers`). The real LineageLens MCP tools are now registered in `arms.ARM_LINEAGELENS.tool_names` and driven into prompts via `render_prompt()`, so the bug class cannot recur.

## Architecture

- `arms.py` — Arm dataclass, 4 concrete registrations, helper `build_mcp_config()`
- `common.py` — Shared utilities: `log`, `run_cmd`, `prepare_clones`, `run_arm_setup`, `parse_claude_jsonl`, `compute_metrics`, `capture_tool_versions`
- `prompts.py` — `render_prompt()` function
- `prompt_pr_template.md`, `prompt_arch_template.md` — Jinja/format templates with `{tool_hints}`, `{tool_only_constraint}` placeholders
- `contamination.py` — Anti-contamination: `build_sanitized_path()`, `parse_run_for_contamination()`
- `report.py` — `write_pr_report()`, `write_arch_report()` for N-column tables
- `run_pr_benchmark.py` — Suite A runner
- `run_arch_benchmark.py` — Suite B runner
- `benchmark.yaml` — Suite A config (default: LangChain PR #39809)
- `arch_benchmark.yaml` — Suite B config (default: Apache Dubbo + LangChain)

## Cost & Time Estimates

**Suite A (single PR, 4 arms):**
- Clone: 2–5 min (depends on repo size)
- Setup: 30 sec – 5 min (LineageLens analyze can be slow on large repos)
- Claude calls: 4–10 min (depends on budget/model)
- **Total**: ~10–20 min; cost ~$3–5 per run

**Suite B (2 repos, 4 arms, 4 runs per arm):**
- Clones: 1–3 min each
- Setup: 30 sec – 5 min each
- Claude calls: 4–10 min × 8 scenarios (2 repos × 4 arms)
- **Total**: ~30–60 min; cost ~$20–30 per run

## Troubleshooting

### MCP Tools Not Being Called (0 tool calls)

**Symptom:** LineageLens/CodeGraph/Graphify shows `Tool calls: 0` but runs in 0.2-1 second

**Causes & Fixes:**
1. **MCP server not starting** — check if the tool's MCP binary is installed
   - LineageLens: `lineagelens-mcp` (installed with `pip install lineagelens`)
   - CodeGraph: `codegraph serve --mcp` (installed with `npm i -g @colbymchenry/codegraph`)
   - Graphify: `python -m graphify.serve` (installed with `uv tool install graphifyy`)

2. **Graph is empty/too small** — tool's indexing failed
   - Check: `ls -la <clone_dir>/.lineagelens/graph.json` (should be >1KB)
   - Or: `ls -la <clone_dir>/.codegraph/` (should have index files)
   - Fix: Re-run setup with `--verbose` flag to see indexing errors

3. **Wrong `source_roots` path** — analyzer scans wrong directory
   - Fix: Edit `benchmark.yaml`, use correct path (e.g., `["libs/langchain/langchain_classic"]`)
   - Check: `cd <clone_dir> && lineagelens analyze . --verbose` to see what's being scanned

### Graph Has Only X Symbols (Expected >20)

- Your `source_roots` config is wrong
- Fix: Edit `benchmark.yaml` and use the actual package directory
- For LangChain: `source_roots: ["libs/langchain/langchain_classic"]`

### CodeGraph/Graphify Installation Fails

- CodeGraph: `npm i -g @colbymchenry/codegraph`
- Graphify: `uv tool install graphifyy`
- Ensure Node 18+ (for CodeGraph) and Python 3.9+ (for Graphify)

### Claude Calls Timeout

- Increase `timeout_seconds` in config (default 900 sec = 15 min)
- Or reduce `budget_usd` to force smaller responses
- Or test locally with a smaller repo first

### "BLOCKED: Benchmark contamination detected" (Suite B only)

- Claude tried to invoke a competitor's CLI via Bash (anti-cheating layer detected it)
- Check if prompt accidentally mentions competitor tool names
- This is expected behavior — contamination detection is working

## References

- CodeGraph benchmark methodology: https://github.com/colbymchenry/codegraph/blob/main/docs/benchmarks/residual-context-occupancy.md
- Graphify benchmarks: https://github.com/Graphify-Labs/graphify/blob/v8/BENCHMARKS.md
- LineageLens MCP server: `src/lineagelens/mcp_server.py`
