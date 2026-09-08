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

## Manual Benchmark Test (Step-by-Step)

**Goal:** Run a single PR question against LineageLens, CodeGraph, Graphify, and Baseline sequentially. Record all metrics (tool calls, tokens, duration, cost) after each run.

All output files go to **`$TEST_OUT`** directory.

### STEP 0: Create Isolated Test Directory

```bash
# Define test directory — all work and outputs here
TEST_DIR="/tmp/ll-bench-manual-test1"
rm -rf $TEST_DIR
mkdir -p $TEST_DIR
cd $TEST_DIR

# All output files go to TEST_OUT
TEST_OUT="$TEST_DIR/results"
mkdir -p $TEST_OUT

echo "Test directory: $TEST_DIR"
echo "Outputs saved to: $TEST_OUT"
```

### STEP 1: Clone Repo (Do Once)

```bash
cd $TEST_DIR

# Clone LangChain at PR base commit
git clone https://github.com/langchain-ai/langchain repo
cd repo
git checkout a2024abe50a1db8dba3884a2a45c91989e6b561e

# Save absolute path for later
REPO_PATH="$(pwd)"
echo "Repo at: $REPO_PATH"
```

### STEP 2: Build ALL Graphs (Do Once Each)

```bash
cd $REPO_PATH

# Build LineageLens graph
echo "=== Building LineageLens graph ==="
lineagelens init .
sed -i '' 's/source_roots: \[\]/source_roots: ["libs\/langchain\/langchain_classic"]/' lineagelens.yaml
lineagelens analyze . --quiet
echo "✓ LineageLens graph ready at .lineagelens/graph.json"

# Build CodeGraph index
echo "=== Building CodeGraph index ==="
codegraph init
echo "✓ CodeGraph index ready at .codegraph/"

# Build Graphify graph
echo "=== Building Graphify graph ==="
graphify extract . --code-only
echo "✓ Graphify graph ready at graphify-out/graph.json"
```

### STEP 3: Define Question & Run Against Each Tool

**PR #39809:** "feat(anthropic): surface gateway response metadata"

**Context:** The LangSmith gateway returns resolved provider and model metadata in response headers. This PR propagates the gateway metadata for tracing purposes.

**Question:** "What files would need to change to implement this feature? List them in '## Files I would change' section."

```bash
cd $REPO_PATH
TEST_OUT=/tmp/ll-bench-manual-test1/results

BASE_PROMPT="PR #39809: feat(anthropic): surface gateway response metadata. The LangSmith gateway returns resolved provider and model metadata in response headers. This PR propagates the gateway metadata for tracing purposes. Which files would need to change to implement this? List them in '## Files I would change' section."

for TOOL in lineagelens codegraph graphify baseline; do
  OUTFILE="$TEST_OUT/${TOOL}.jsonl"
  echo "=== TEST: $TOOL ==="
  echo "Output: $OUTFILE"
  echo ""
  
  if [ "$TOOL" = "lineagelens" ]; then
    PROMPT="$BASE_PROMPT

TOOL USAGE - LINEAGELENS MCP ONLY:
- You have access to LineageLens via MCP (search_symbols, get_callers, get_impact, get_lineage tools)
- DO NOT read files directly using Read or Bash commands
- DO NOT use grep, find, or any file exploration
- DO NOT use CodeGraph or Graphify
- ONLY use LineageLens MCP tools to search and understand the codebase
- Start by searching for 'anthropic', 'chat_models', 'gateway', 'response_metadata'
- Use get_lineage to understand how the anthropic chat models work
- Use get_callers to find what depends on the chat models
- Identify files that would need changes using only LineageLens"
    
    claude -p "$PROMPT" \
      --mcp-config '{"mcpServers":{"lineagelens":{"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"'$REPO_PATH'"}}}}' \
      --allowedTools "mcp__lineagelens__*" \
      --disallowedTools "mcp__lineagelens__trigger_analysis,Read,Glob,Grep,Bash" \
      --model claude-sonnet-5 \
      --max-budget-usd 2.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  elif [ "$TOOL" = "codegraph" ]; then
    PROMPT="$BASE_PROMPT

TOOL USAGE - CODEGRAPH MCP ONLY:
- You have access to CodeGraph via MCP (codegraph_explore, codegraph_search, codegraph_find_references)
- DO NOT read files directly using Read or Bash commands
- DO NOT use grep, find, or any file exploration
- DO NOT use LineageLens or Graphify
- ONLY use CodeGraph MCP tools to explore the codebase
- Start by exploring 'anthropic', 'chat_models', 'gateway', 'response'
- Use codegraph_explore to understand relationships and dependencies
- Find what files import or use the anthropic chat models
- Identify all files that would need changes based on CodeGraph analysis only"
    
    claude -p "$PROMPT" \
      --mcp-config '{"mcpServers":{"codegraph":{"type":"stdio","command":"codegraph","args":["serve","--mcp"]}}}' \
      --allowedTools "mcp__codegraph__*" \
      --disallowedTools "Read,Glob,Grep,Bash,mcp__lineagelens__*" \
      --model claude-sonnet-5 \
      --max-budget-usd 2.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  elif [ "$TOOL" = "graphify" ]; then
    PROMPT="$BASE_PROMPT

TOOL USAGE - GRAPHIFY CLI ONLY:
- You MUST use ONLY Graphify CLI commands via the Bash tool
- DO NOT read actual Python files from the repository using Read
- DO NOT use grep, find, or any file exploration tools
- DO NOT use CodeGraph, LineageLens, or any other tools
- ONLY available tool: Bash with 'graphify' commands
- Available Graphify commands:
  * graphify query \"<search terms>\" - search for symbols/classes
  * graphify path \"A\" \"B\" - find shortest path between symbols
  * graphify explain \"<symbol>\" - get details about a symbol
  * graphify affected \"<symbol>\" - find what depends on this symbol
- Example: graphify query \"anthropic chat_models gateway\"
- Then: graphify affected \"ChatAnthropicApi\" to find dependents
- MUST use Graphify CLI exclusively
- Identify files using only Graphify analysis"
    
    claude -p "$PROMPT" \
      --disallowedTools "mcp__codegraph__*,mcp__lineagelens__*,Bash(grep *),Bash(find *),Read" \
      --allowedTools "Bash(graphify *),Glob" \
      --model claude-sonnet-5 \
      --max-budget-usd 2.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  else
    PROMPT="$BASE_PROMPT

TOOL USAGE - FILE EXPLORATION ONLY (BASELINE):
- You have access to Read, Glob, Grep, and Bash(find *) only
- DO NOT use LineageLens, CodeGraph, or Graphify
- Use these tools to explore the repository structure
- Strategy:
  1. Find Python files related to 'anthropic', 'chat_models', 'gateway'
  2. Use Grep to search for 'response_metadata', 'gateway', 'anthropic'
  3. Read files to understand their content and dependencies
  4. Identify which files would need changes
- Look for imports and references to understand the dependency chain
- List only files that likely need modification based on static exploration"
    
    claude -p "$PROMPT" \
      --allowedTools "Read,Glob,Grep,Bash(find *)" \
      --disallowedTools "mcp__codegraph__*,mcp__lineagelens__*" \
      --model claude-sonnet-5 \
      --max-budget-usd 2.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  fi
  
  echo "✓ Saved to $OUTFILE"
done
```

### STEP 4: Collect & Analyze All Results

After all 4 tests complete, extract metrics AND analyze quality:

```bash
TEST_OUT=/tmp/ll-bench/ll-bench-manual-test1/results

echo "=== COMPREHENSIVE BENCHMARK ANALYSIS ==="
python3 << "EOFPYTHON"
import json
import re

# Ground truth files changed in PR #39809
GROUND_TRUTH = {
    'libs/partners/anthropic/langchain_anthropic/chat_models.py',
    'libs/partners/anthropic/tests/unit_tests/test_chat_models.py',
    'libs/partners/anthropic/uv.lock',
}

TEST_OUT = "$TEST_OUT"

tools = [
    ('lineagelens', 'LineageLens'),
    ('codegraph', 'CodeGraph'),
    ('graphify', 'Graphify'),
    ('baseline', 'Baseline'),
]

results = {}

def extract_files_from_response(response_text):
    """Extract file paths from '## Files I would change' section."""
    files = set()
    if '## Files I would change' in response_text:
        section = response_text.split('## Files I would change')[1]
        # Split at next ## or end of text
        if '##' in section[1:]:
            section = section[:section.index('\n##')]
        # Find all file paths (lines with .py, .lock, etc)
        lines = section.split('\n')
        for line in lines:
            line = line.strip()
            if line and ('/' in line or line.endswith(('.py', '.lock', '.ts', '.js', '.java', '.txt'))):
                # Remove markdown formatting
                line = line.lstrip('- * >')
                if line and not line.startswith('**'):
                    files.add(line)
    return files

def calculate_f1(predicted, ground_truth):
    """Calculate precision, recall, F1."""
    tp = len(predicted & ground_truth)
    fp = len(predicted - ground_truth)
    fn = len(ground_truth - predicted)
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    return precision, recall, f1

# Parse each tool's output
for fname, label in tools:
    try:
        outfile = f'{TEST_OUT}/{fname}.jsonl'
        with open(outfile) as f:
            lines = f.readlines()
            tool_calls = 0
            tokens_in = 0
            tokens_out = 0
            cost = 0
            response_text = ''
            
            for line in lines:
                if line.strip():
                    obj = json.loads(line)
                    if obj.get('type') == 'assistant':
                        calls = obj.get('message', {}).get('content', [])
                        tool_calls = len([c for c in calls if c.get('type') == 'tool_use'])
                    elif obj.get('type') == 'result':
                        usage = obj.get('usage', {})
                        tokens_in = usage.get('input_tokens', 0)
                        tokens_out = usage.get('output_tokens', 0)
                        cost = obj.get('total_cost_usd', 0)
                        response_text = obj.get('result', '')
            
            # Extract files identified
            identified_files = extract_files_from_response(response_text)
            precision, recall, f1 = calculate_f1(identified_files, GROUND_TRUTH)
            
            results[label] = {
                'tool_calls': tool_calls,
                'tokens_in': tokens_in,
                'tokens_out': tokens_out,
                'cost': cost,
                'files_identified': identified_files,
                'precision': precision,
                'recall': recall,
                'f1': f1,
                'response_preview': response_text[:300],
            }
    except Exception as e:
        results[label] = {'error': str(e)}

# Print EFFICIENCY table (tool calls, tokens, cost)
print("\n" + "="*90)
print("EFFICIENCY METRICS")
print("="*90)
print("| Tool       | Tool Calls | Tokens In | Tokens Out | Cost (USD) |")
print("|------------|-----------|-----------|-----------|-----------|")
for label, data in results.items():
    if 'error' not in data:
        print(f"| {label:10} | {data['tool_calls']:9} | {data['tokens_in']:9} | {data['tokens_out']:10} | ${data['cost']:8.4f} |")
    else:
        print(f"| {label:10} | ERROR: {data['error'][:30]}")

# Print QUALITY table (F1, precision, recall)
print("\n" + "="*90)
print("QUALITY METRICS (vs Ground Truth)")
print("="*90)
print(f"Ground truth files: {len(GROUND_TRUTH)}")
for f in sorted(GROUND_TRUTH):
    print(f"  - {f}")
print("\n| Tool       | Files Found | Precision | Recall | F1 Score |")
print("|------------|-------------|-----------|--------|----------|")
for label, data in results.items():
    if 'error' not in data:
        print(f"| {label:10} | {len(data['files_identified']):11} | {data['precision']:.2%} | {data['recall']:.2%} | {data['f1']:.3f}   |")
        if data['files_identified']:
            print(f"            Files: {', '.join(sorted(list(data['files_identified'])[:2]))}")
    else:
        print(f"| {label:10} | ERROR")

# Print EFFICIENCY WINNER
print("\n" + "="*90)
print("ANALYSIS")
print("="*90)
sorted_by_cost = sorted(results.items(), key=lambda x: x[1].get('cost', float('inf')))
sorted_by_f1 = sorted(results.items(), key=lambda x: x[1].get('f1', 0), reverse=True)

if sorted_by_cost[0][1].get('cost'):
    print(f"✓ Cheapest: {sorted_by_cost[0][0]} (${sorted_by_cost[0][1]['cost']:.4f})")
if sorted_by_f1[0][1].get('f1'):
    print(f"✓ Best Quality (F1): {sorted_by_f1[0][0]} (F1={sorted_by_f1[0][1]['f1']:.3f})")

# Best efficiency = quality-per-dollar
print("\n✓ Quality-per-Dollar (F1 / Cost):")
for label, data in sorted(results.items()):
    if 'error' not in data and data['cost'] > 0:
        efficiency = data['f1'] / data['cost']
        print(f"  {label:12}: {efficiency:.2f} (F1={data['f1']:.3f} / ${data['cost']:.4f})")

print(f"\nAll outputs saved to: {TEST_OUT}")
print("ls -lh results:")
import os
for f in sorted(os.listdir(TEST_OUT)):
    path = os.path.join(TEST_OUT, f)
    size = os.path.getsize(path)
    print(f"  {f:30} {size:10} bytes")
EOFPYTHON
```

### Expected Output

A table like:

| Tool | Tool Calls | Tokens In | Tokens Out | Cost (USD) |
|------|-----------|-----------|-----------|-----------|
| LineageLens | 12 | 2500 | 850 | $0.15 |
| CodeGraph | 8 | 2400 | 820 | $0.14 |
| Graphify | 15 | 2600 | 890 | $0.16 |
| Baseline | 28 | 4200 | 1200 | $0.22 |

---

## Benchmark Test 2: Apache Dubbo (Java)

**Goal:** Same methodology, but on a Java project (Apache Dubbo). Tests tools on both PR-replication and architecture questions.

### STEP 0: Create Isolated Test Directory

```bash
TEST_DIR="/tmp/ll-bench-dubbo-test1"
rm -rf $TEST_DIR
mkdir -p $TEST_DIR
cd $TEST_DIR

TEST_OUT="$TEST_DIR/results"
mkdir -p $TEST_OUT

echo "Test directory: $TEST_DIR"
echo "Outputs saved to: $TEST_OUT"
```

### STEP 1: Clone Dubbo Repo

```bash
cd $TEST_DIR

# Clone Apache Dubbo
git clone https://github.com/apache/dubbo repo
cd repo

REPO_PATH="$(pwd)"
echo "Repo at: $REPO_PATH"
```

### STEP 1.5: Checkout PR Base Commit

```bash
cd $REPO_PATH

# PR #16416: Fix Triple gRPC decoder handoff
# Base commit: 3a3043227f5571d25eb2889de5bca22f2914843b
git checkout 3a3043227f5571d25eb2889de5bca22f2914843b

echo "✓ Checked out at PR base commit"
```

### STEP 2: Build ALL Graphs

```bash
cd $REPO_PATH

# Build LineageLens graph (Java project)
echo "=== Building LineageLens graph ==="
lineagelens init .
# For Dubbo, source roots are typically the main source directory
lineagelens analyze . --quiet
echo "✓ LineageLens graph ready"

# Build CodeGraph index
echo "=== Building CodeGraph index ==="
codegraph init
echo "✓ CodeGraph index ready"

# Build Graphify graph
echo "=== Building Graphify graph ==="
graphify extract . --code-only
echo "✓ Graphify graph ready"
```

### STEP 3a: PR-Replication Test (Dubbo PR #16416)

**PR #16416:** "Fix Triple gRPC decoder handoff"

**Context:** This PR fixes the gRPC no-stub method discovery path for Triple streaming requests. The lazy method discovery listener now reuses the stream's existing `StreamingDecoder` instead of creating a temporary one.

**Question:** "What files would need to change to implement this gRPC streaming decoder fix?"

```bash
cd $REPO_PATH
TEST_OUT=/tmp/ll-bench-dubbo-test1/results

# PREREQUISITE: Install CodeGraph and Graphify globally (one-time only)
# Note: Both require installation to work with Claude; verify with:
#   codegraph --version        (should be installed)
#   graphify --version         (should be installed)
#   codegraph install --yes    (one-time: registers MCP server globally)
#   graphify install --platform claude  (one-time: registers Claude skill)

for TOOL in lineagelens codegraph graphify baseline; do
  OUTFILE="$TEST_OUT/dubbo-pr-${TOOL}.jsonl"
  echo "=== Running $TOOL (PR-replication) ==="
  
  BASE_PROMPT="Dubbo PR #16416: Fix Triple gRPC decoder handoff. The lazy method discovery listener creates a temporary GrpcStreamingDecoder, which loses buffered bytes from subsequent messages. The fix reuses the existing StreamingDecoder and makes the close callback a no-op. Which files would need to change? List them in '## Files I would change' section."
  
  if [ "$TOOL" = "lineagelens" ]; then
    PROMPT="$BASE_PROMPT

TOOL USAGE - LINEAGELENS MCP ONLY:
- You have access to LineageLens via MCP (search_symbols, get_callers, get_impact, get_lineage tools)
- DO NOT read files directly using Read or Bash commands
- DO NOT use grep, find, or any file exploration
- DO NOT use CodeGraph or Graphify
- ONLY use LineageLens MCP tools to search the codebase
- Example: search for 'GrpcStreamingDecoder' and 'LazyFindMethodListener' using LineageLens
- Start with searching for the class names mentioned in the PR description
- Use get_lineage to understand how classes depend on each other
- List only files that would need modification"
    
    claude -p "$PROMPT" \
      --mcp-config '{"mcpServers":{"lineagelens":{"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"'$REPO_PATH'"}}}}' \
      --allowedTools "mcp__lineagelens__*" \
      --disallowedTools "mcp__lineagelens__trigger_analysis,Read,Glob,Grep,Bash" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  elif [ "$TOOL" = "codegraph" ]; then
    # CodeGraph MCP server (proper setup with `codegraph install --yes`)
    PROMPT="$BASE_PROMPT

TOOL USAGE - CODEGRAPH MCP ONLY:
- You have access to CodeGraph via MCP (codegraph_explore, codegraph_search, codegraph_find_references)
- DO NOT read files directly using Read or Bash commands
- DO NOT use grep, find, or any file exploration
- DO NOT use LineageLens or Graphify
- ONLY use CodeGraph MCP tools to query the codebase graph
- Example: Use codegraph_explore to understand 'GrpcStreamingDecoder' and 'LazyFindMethodListener' relationships
- Search for callers and dependencies of the key classes
- Trace which files import or use these classes
- List only files that would need modification based on CodeGraph analysis"
    
    claude -p "$PROMPT" \
      --mcp-config '{"mcpServers":{"codegraph":{"type":"stdio","command":"codegraph","args":["serve","--mcp"]}}}' \
      --allowedTools "mcp__codegraph__*" \
      --disallowedTools "Read,Glob,Grep,Bash,mcp__lineagelens__*" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  elif [ "$TOOL" = "graphify" ]; then
    # Graphify CLI (no MCP server; use Bash with explicit constraints in prompt)
    PROMPT="$BASE_PROMPT

TOOL USAGE - GRAPHIFY CLI ONLY:
- You MUST use ONLY Graphify CLI commands via the Bash tool
- DO NOT read actual Java files from the repository using Read
- DO NOT use grep, find, or any file exploration tools
- DO NOT use CodeGraph, LineageLens, or any other graph tools
- ONLY available tool: Bash with 'graphify' commands
- Available Graphify commands:
  * graphify query \"<search terms>\" - search for symbols/classes
  * graphify path \"A\" \"B\" - find shortest path between symbols
  * graphify explain \"<symbol>\" - get details about a symbol
  * graphify affected \"<symbol>\" - find what depends on this symbol
- Example usage: graphify query \"GrpcStreamingDecoder LazyFindMethodListener\"
- Then: graphify affected \"GrpcStreamingDecoder\" to see dependents
- Then: graphify path \"LazyFindMethodListener\" \"GrpcStreamingDecoder\" for relationships
- MUST use Graphify CLI exclusively to answer this question
- List only files identified by Graphify analysis"
    
    claude -p "$PROMPT" \
      --disallowedTools "mcp__codegraph__*,mcp__lineagelens__*,Bash(grep *),Bash(find *),Read" \
      --allowedTools "Bash(graphify *),Glob" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  else
    # Baseline: file exploration only
    PROMPT="$BASE_PROMPT

TOOL USAGE - FILE EXPLORATION ONLY (BASELINE):
- You have access to Read, Glob, Grep, and Bash(find *) only
- DO NOT use LineageLens, CodeGraph, or Graphify
- Use these tools to explore the repository structure and find relevant files
- Strategy:
  1. Use Glob to find all Java files (*.java)
  2. Use Grep to search for 'GrpcStreamingDecoder' and 'LazyFindMethodListener'
  3. Read relevant files to understand their purpose
  4. Identify which files need changes based on static code analysis
- Look for files that import or reference the classes mentioned in the PR
- List only files that likely need modification based on file content"
    
    claude -p "$PROMPT" \
      --allowedTools "Read,Glob,Grep,Bash(find *)" \
      --disallowedTools "mcp__codegraph__*,mcp__lineagelens__*" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  fi
  
  echo "✓ Saved to $OUTFILE"
done
```

### STEP 3b: Architecture Question Test (Dubbo)

**Question:** "How does Dubbo's service registration flow from provider startup to registry write? Trace the complete call chain from service export to registry operations."

```bash
cd $REPO_PATH
TEST_OUT=/tmp/ll-bench-dubbo-test1/results

for TOOL in lineagelens codegraph graphify baseline; do
  OUTFILE="$TEST_OUT/dubbo-arch-${TOOL}.jsonl"
  echo "=== Running $TOOL (architecture question) ==="
  
  BASE_PROMPT="Dubbo Service Registration Architecture: Trace the complete call chain from provider startup to registry write. How does a service flow through the startup process, through export, and finally to registry operations? Describe the key classes, methods, and flow."
  
  if [ "$TOOL" = "lineagelens" ]; then
    PROMPT="$BASE_PROMPT

TOOL USAGE - LINEAGELENS MCP ONLY:
- You have access to LineageLens via MCP (search_symbols, get_callers, get_impact, get_lineage tools)
- DO NOT read files directly using Read or Bash commands
- DO NOT use grep, find, or any file exploration
- DO NOT use CodeGraph or Graphify
- ONLY use LineageLens MCP tools to trace the architecture
- Strategy:
  1. Search for 'ServiceExporter' or 'ServiceConfig' as entry points
  2. Use get_lineage to trace the call chain
  3. Use get_callers to find what invokes each method
  4. Follow the chain to 'Registry' or 'RegistryService'
- Describe the complete call flow using only LineageLens findings
- Do not read actual source files"
    
    claude -p "$PROMPT" \
      --mcp-config '{"mcpServers":{"lineagelens":{"command":"lineagelens-mcp","env":{"LINEAGELENS_PROJECT":"'$REPO_PATH'"}}}}' \
      --allowedTools "mcp__lineagelens__*" \
      --disallowedTools "mcp__lineagelens__trigger_analysis,Read,Glob,Grep,Bash" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  elif [ "$TOOL" = "codegraph" ]; then
    # CodeGraph MCP server (after running `codegraph install --yes`)
    PROMPT="$BASE_PROMPT

TOOL USAGE - CODEGRAPH MCP ONLY:
- You have access to CodeGraph via MCP (codegraph_explore, codegraph_search, codegraph_find_references)
- DO NOT read files directly using Read or Bash commands
- DO NOT use grep, find, or any file exploration
- DO NOT use LineageLens or Graphify
- ONLY use CodeGraph MCP tools to understand the architecture
- Strategy:
  1. Use codegraph_explore on 'ServiceExporter' and 'ServiceConfig'
  2. Explore their callers and dependencies
  3. Trace the path to Registry-related classes
  4. Understand the relationships and call flow
- Provide detailed architecture explanation using only CodeGraph data
- Do not read actual source files"
    
    claude -p "$PROMPT" \
      --mcp-config '{"mcpServers":{"codegraph":{"type":"stdio","command":"codegraph","args":["serve","--mcp"]}}}' \
      --allowedTools "mcp__codegraph__*" \
      --disallowedTools "Read,Glob,Grep,Bash,mcp__lineagelens__*" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  elif [ "$TOOL" = "graphify" ]; then
    # Graphify CLI with constraints to force usage (no MCP server available)
    PROMPT="$BASE_PROMPT

TOOL USAGE - GRAPHIFY CLI ONLY:
- You MUST use ONLY Graphify CLI commands via the Bash tool
- DO NOT read actual Java files from the repository using Read
- DO NOT use grep, find, or any file exploration tools  
- DO NOT use CodeGraph, LineageLens, or any other tools
- ONLY available tool: Bash with 'graphify' commands
- Available Graphify commands:
  * graphify query \"<search terms>\" - search for symbols
  * graphify path \"A\" \"B\" - find path between symbols
  * graphify affected \"<symbol>\" --depth N - find dependents
  * graphify explain \"<symbol>\" - get symbol details
  * graphify god-nodes - find central architectural hubs
- Example workflow:
  1. graphify god-nodes - find main classes
  2. graphify explain \"ServiceExporter\"
  3. graphify path \"ServiceExporter\" \"Registry\" - trace flow
  4. graphify affected \"Registry\" --depth 3 - see consumers
- MUST use Graphify CLI exclusively to understand the architecture
- Describe the complete service registration flow using only Graphify data"
    
    claude -p "$PROMPT" \
      --disallowedTools "mcp__codegraph__*,mcp__lineagelens__*,Bash(grep *),Bash(find *),Read" \
      --allowedTools "Bash(graphify *),Glob" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  
  else
    # Baseline: file exploration only
    PROMPT="$BASE_PROMPT

TOOL USAGE - FILE EXPLORATION ONLY (BASELINE):
- You have access to Read, Glob, Grep, and Bash(find *) only
- DO NOT use LineageLens, CodeGraph, or Graphify
- Use these tools to explore the codebase structure and understand architecture
- Strategy:
  1. Find files related to 'ServiceExporter', 'ServiceConfig', 'Registry'
  2. Use Grep to find class definitions and method calls
  3. Read relevant files to understand the flow
  4. Trace method calls to understand the complete chain
- Look for startup methods, export methods, and registry operations
- Piece together the complete architecture from static code analysis
- Describe the service registration flow based on file exploration"
    
    claude -p "$PROMPT" \
      --allowedTools "Read,Glob,Grep,Bash(find *)" \
      --disallowedTools "mcp__codegraph__*,mcp__lineagelens__*" \
      --model claude-sonnet-5 \
      --max-budget-usd 3.0 \
      --output-format stream-json \
      --verbose > "$OUTFILE"
  fi
  
  echo "✓ Saved to $OUTFILE"
done
```

### STEP 4: Analyze Dubbo Results

```bash
TEST_OUT=/tmp/ll-bench-dubbo-test1/results

python3 << 'EOFPY'
import json
import re
import os

# For PR test, these are the key registry-related files we'd expect to change
DUBBO_PR_FILES = {
    'dubbo-registry/dubbo-registry-api/src/main/java/org/apache/dubbo/registry/Registry.java',
    'dubbo-common/src/main/java/org/apache/dubbo/common/utils/StringUtils.java',
    'dubbo-registry/dubbo-registry-default/src/main/java/org/apache/dubbo/registry/dubbo/DubboRegistry.java',
}

# Architecture question looks for call chains (just count files mentioned)
tools = [
    ('dubbo-pr-lineagelens', 'LineageLens (PR)'),
    ('dubbo-pr-codegraph', 'CodeGraph (PR)'),
    ('dubbo-pr-graphify', 'Graphify (PR)'),
    ('dubbo-pr-baseline', 'Baseline (PR)'),
    ('dubbo-arch-lineagelens', 'LineageLens (Arch)'),
    ('dubbo-arch-codegraph', 'CodeGraph (Arch)'),
    ('dubbo-arch-graphify', 'Graphify (Arch)'),
    ('dubbo-arch-baseline', 'Baseline (Arch)'),
]

results = {}

def count_tool_calls(jsonl_content):
    tool_calls = 0
    for line in jsonl_content.split('\n'):
        if line.strip():
            try:
                obj = json.loads(line)
                if obj.get('type') == 'assistant':
                    calls = obj.get('message', {}).get('content', [])
                    tool_calls += len([c for c in calls if c.get('type') == 'tool_use'])
            except:
                pass
    return tool_calls

def extract_files(response_text):
    file_pattern = r'(?:src/main/java/)?org/apache/dubbo/[^\s`\)]+\.java'
    return set(re.findall(file_pattern, response_text))

for fname, label in tools:
    try:
        outfile = f'{TEST_OUT}/{fname}.jsonl'
        with open(outfile) as f:
            content = f.read()
            tool_calls = count_tool_calls(content)
            tokens_in = 0
            tokens_out = 0
            cost = 0
            response_text = ''
            
            for line in content.split('\n'):
                if line.strip():
                    try:
                        obj = json.loads(line)
                        if obj.get('type') == 'result':
                            usage = obj.get('usage', {})
                            tokens_in = usage.get('input_tokens', 0)
                            tokens_out = usage.get('output_tokens', 0)
                            cost = obj.get('total_cost_usd', 0)
                            response_text = obj.get('result', '')
                    except:
                        pass
            
            files = extract_files(response_text)
            
            results[label] = {
                'tool_calls': tool_calls,
                'tokens_in': tokens_in,
                'tokens_out': tokens_out,
                'cost': cost,
                'files_found': len(files),
            }
            
    except:
        results[label] = {'error': 'file not found'}

# Print results
print("\n" + "="*100)
print("DUBBO BENCHMARK RESULTS (PR-Replication Test)")
print("="*100)
print("| Tool            | Tool Calls | Tokens In | Tokens Out | Cost (USD) | Files Found |")
print("|-----------------|-----------|-----------|-----------|-----------|------------|")
for label in ['LineageLens (PR)', 'CodeGraph (PR)', 'Graphify (PR)', 'Baseline (PR)']:
    data = results[label]
    if 'error' not in data:
        print(f"| {label:15} | {data['tool_calls']:9} | {data['tokens_in']:9} | {data['tokens_out']:10} | ${data['cost']:8.4f} | {data['files_found']:10} |")

print("\n" + "="*100)
print("DUBBO BENCHMARK RESULTS (Architecture Question Test)")
print("="*100)
print("| Tool            | Tool Calls | Tokens In | Tokens Out | Cost (USD) | Files Found |")
print("|-----------------|-----------|-----------|-----------|-----------|------------|")
for label in ['LineageLens (Arch)', 'CodeGraph (Arch)', 'Graphify (Arch)', 'Baseline (Arch)']:
    data = results[label]
    if 'error' not in data:
        print(f"| {label:15} | {data['tool_calls']:9} | {data['tokens_in']:9} | {data['tokens_out']:10} | ${data['cost']:8.4f} | {data['files_found']:10} |")
EOFPY
```

---

## Benchmark Results (Real Data)

### Dubbo PR #16416 Benchmark Results

**Efficiency Metrics:**

| Tool              | Calls | Tokens In | Tokens Out | Cost    | Cost/Call |
|-------------------|-------|-----------|------------|---------|-----------|
| CodeGraph (MCP)   | 5     | 12        | 7,088      | $0.3411 | $0.0682   |
| Graphify (CLI)    | 25    | 28        | 9,592      | $0.6018 | $0.0241   |
| LineageLens (MCP) | 30    | 132       | 5,359      | $0.3234 | $0.0108   |
| Baseline (Files)  | 5     | 34        | 1,582      | $0.1141 | $0.0228   |

**Quality Metrics (vs Ground Truth - 2 files):**

| Tool              | Found | Precision | Recall | F1     |
|-------------------|-------|-----------|--------|--------|
| CodeGraph (MCP)   | 6     | 16.7%     | 50.0%  | 0.250  |
| Graphify (CLI)    | 5     | 20.0%     | 50.0%  | 0.286  |
| LineageLens (MCP) | 3     | 0%        | 0%     | 0.000  |
| Baseline (Files)  | 6     | 0%        | 0%     | 0.000  |

**Winner: CodeGraph (MCP) - Best value at 0.73 F1/$ with fewest tool calls**

**Key Findings:**
1. **CodeGraph** with proper MCP installation (`codegraph install --yes`) achieves best efficiency: 5 calls, finds correct file
2. **Graphify** CLI works but expensive: 25 commands, higher token usage, slightly better F1 (0.286 vs 0.250)
3. **LineageLens** found wrong files on Dubbo (Java) - precision/recall both 0%, suggesting Java edge types not fully covered
4. **Baseline** file exploration also found no correct files, showing the value of graph tools for this task

### Ground Truth Reference

**LangChain PR #39809:** 3 files changed
- `libs/partners/anthropic/langchain_anthropic/chat_models.py`
- `libs/partners/anthropic/tests/unit_tests/test_chat_models.py`
- `libs/partners/anthropic/uv.lock`

**Dubbo PR #16416 (gRPC Decoder Fix):** 2 files changed
- `dubbo-rpc/dubbo-rpc-triple/src/main/java/org/apache/dubbo/rpc/protocol/tri/h12/grpc/GrpcHttp2ServerTransportListener.java`
- `dubbo-rpc/dubbo-rpc-triple/src/test/java/org/apache/dubbo/rpc/protocol/tri/h12/grpc/GrpcStreamingDecoderTest.java`

## Files to Compare Against

Ground truth for PR #39809 (actual files changed):
```
libs/partners/anthropic/langchain_anthropic/chat_models.py
libs/partners/anthropic/tests/unit_tests/test_chat_models.py
libs/partners/anthropic/uv.lock
```

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

### CodeGraph MCP Not Working

**Symptom:** CodeGraph uses file exploration (grep/Read) instead of MCP, or 0 tool calls

**Causes & Fixes:**
1. **CodeGraph not installed globally** — required once per machine
   ```bash
   npm i -g @colbymchenry/codegraph
   codegraph install --yes --location global --target auto
   # Verify: codegraph --version && which codegraph
   ```

2. **CodeGraph index doesn't exist in repo** — requires per-project setup
   ```bash
   cd $REPO_PATH
   codegraph init  # Creates .codegraph/ directory
   # Verify: ls -la .codegraph/
   ```

3. **MCP config wrong** — use exact format:
   ```bash
   --mcp-config '{"mcpServers":{"codegraph":{"type":"stdio","command":"codegraph","args":["serve","--mcp"]}}}'
   --allowedTools "mcp__codegraph__*"   # Force MCP-only usage
   ```

### Graphify CLI Not Working

**Symptom:** Claude uses grep/Read instead of Graphify CLI commands

**Causes & Fixes:**
1. **Graphify not installed** — check and install
   ```bash
   uv tool install graphifyy
   graphify install --platform claude
   # Verify: graphify --version
   ```

2. **Graphify graph doesn't exist** — must extract once per project
   ```bash
   cd $REPO_PATH
   graphify extract . --code-only --no-cluster
   # Verify: ls -la graphify-out/graph.json
   ```

3. **Tools not properly constrained** — must explicitly block file tools:
   ```bash
   --disallowedTools "mcp__codegraph__*,Bash(grep *),Bash(find *),Read"
   --allowedTools "Bash(graphify *),Glob"
   ```
   Plus add explicit constraints in the prompt (see STEP 3a for example).

### MCP Tools Not Being Called (0 tool calls)

**Symptom:** LineageLens/CodeGraph/Graphify shows `Tool calls: 0` but runs in 0.2-1 second

**Causes & Fixes:**
1. **MCP server not starting** — check if the tool's MCP binary is installed
   - LineageLens: `lineagelens-mcp` (installed with `pip install lineagelens`)
   - CodeGraph: `codegraph serve --mcp` (installed with `npm i -g @colbymchenry/codegraph` + `codegraph install --yes`)
   - Graphify: No MCP server — must use CLI via Bash (not applicable)

2. **Graph is empty/too small** — tool's indexing failed
   - Check: `ls -la <clone_dir>/.lineagelens/graph.json` (should be >1KB)
   - Or: `ls -la <clone_dir>/.codegraph/` (should have index files)
   - Or: `ls -la <clone_dir>/graphify-out/graph.json` (should be >1KB)
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
