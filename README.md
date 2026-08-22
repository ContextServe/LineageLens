# LineageLens

**Evidence-labelled Python code lineage for humans and coding agents.**

Understand large Python codebases without reading full source files. Query code structure with a fraction of the token cost. Make safe refactoring decisions with impact analysis.

[![Tests](https://img.shields.io/badge/tests-passing-green)](https://github.com/lineagelens/lineagelens)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org/)
[![MIT License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

LineageLens analyzes Python codebases and creates an **evidence-labelled code graph** showing:
- **Symbols**: Functions, classes, modules, and their contracts (inputs/outputs)
- **Relations**: Who calls what, with argument mapping and evidence confidence
- **Entry Points**: API routes, CLI commands, tests
- **Risks**: Data writes, blocking operations in async code
- **Lineage**: Full transitive call chains for understanding impact

Every signal is **evidence-labelled** so you always know if it's a fact (AST-extracted), a heuristic (static inference), or probabilistic (LLM-generated).

---

## ⚡ Quick Start

### 1. Install

```bash
# Basic CLI usage
pip install lineagelens

# With web UI
pip install lineagelens[web]

# With Claude Code integration
pip install lineagelens[mcp]

# With all features
pip install lineagelens[web,mcp,llm]
```

### 2. Initialize Configuration

```bash
cd /path/to/your/project
lineagelens init .
```

Creates `lineagelens.yaml` with auto-detected configuration:
- **source_roots**: Where your Python code lives (auto-detects `src/`, `app/`, etc.)
- **test_roots**: Where tests are located (auto-detects `tests/`, `test/`, etc.)
- **frameworks**: What frameworks you use (auto-scans imports and dependencies)

The init command prints a summary of what it found:
```
✓ Detected source roots: src
✓ Detected test roots: tests
✓ Detected frameworks: fastapi

Configuration written:
  source_roots: ['src']
  test_roots: ['tests']
  frameworks: ['fastapi']

Next steps:
  1. Review the configuration in lineagelens.yaml
  2. Run: lineagelens analyze .
  3. Optionally run: lineagelens serve . (for web UI)
```

You can edit `lineagelens.yaml` to customize risk rules and entry points (see [Configuration](#%EF%B8%8F-configuration) below).

### 3. Analyze Your Project

```bash
lineagelens analyze .
```

Creates:
- `.lineagelens/graph.json` — Code structure (symbols, relations, containers)
- `.lineagelens/report.json` — Analysis summary with any failures or warnings

### 4. Choose How to Use

---

## 🎯 Four Ways to Use LineageLens

**For all options**, start with initialization and analysis:
```bash
lineagelens init .       # Auto-detect config, creates lineagelens.yaml
lineagelens analyze .    # Build graph.json and report.json
```

Then choose your integration below.

---

### Option A: Claude Code (Easiest — No Hosting Required)

Let Claude understand your codebase with 9 specialized tools via MCP.

```bash
pip install lineagelens[mcp]
```

Add to `.claude/settings.json`:
```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "lineagelens-mcp",
      "env": { "LINEAGELENS_PROJECT": "/path/to/your/project" }
    }
  }
}
```

**Then ask Claude**:
```
"Show me an overview of the app.api module"
→ Claude queries without reading files, saves ~10x tokens

"What would break if I refactored User.find?"  
→ Claude shows full blast radius (impact analysis)

"List all database operations"
→ Claude finds all data_write risk signals
```

📖 **Full guide**: `docs/claude-mcp-setup.md`

---

### Option B: VS Code Extension (IDE Integration)

Interactive graph visualization in your editor.

**Install**: Search "LineageLens" in VS Code Extensions

**Shortcuts**:
- `Ctrl+Shift+P` → "Analyze Workspace"
- `Ctrl+Shift+L` (on symbol) → Show lineage
- Interactive Cytoscape.js graph with zoom/pan
- Click to highlight call flows
- Analysis failures in Problems panel

📖 **Full guide**: `editors/vscode/README.md`

---

### Option C: ChatGPT Actions (Cloud — Requires Hosting)

Give GPT-4 the ability to query your codebase.

**Deploy**:
```bash
docker build -t lineagelens .
# Deploy to Fly.io, Render, or Railway (~$5-50/month)
```

**Use in ChatGPT**:
```
"Help me understand the fetch_user function"
→ GPT-4 queries your backend, explains the code

"What's the impact of removing the cache module?"
→ GPT-4 shows affected entry points and code paths
```

📖 **Full guide**: `docs/deploy-chatgpt-actions.md`

---

### Option D: Web UI (Local Browser)

Interactive visualization served locally.

```bash
pip install lineagelens[web]
lineagelens serve .
# Open http://localhost:8717
```

Features:
- Search any symbol
- Explore call relationships
- View inputs/outputs/risks
- Module-based navigation

---

### Option E: CLI Only (Headless/CI)

```bash
lineagelens analyze /path/to/project

# Results in:
# - .lineagelens/graph.json (code structure)
# - .lineagelens/report.json (analysis summary with failures)

# For CI, use --strict flag:
lineagelens analyze . --strict
# Exit code 1 if any analysis failures
```

---

## 🔧 Configuration

### Auto-Detection with `init`

```bash
lineagelens init .
```

The `init` command auto-detects your project structure and creates `lineagelens.yaml` with:
- **source_roots**: Scans for `src/`, `app/`, `lib/` directories and Python packages
- **test_roots**: Finds `tests/`, `test/`, `spec/` directories
- **frameworks**: Parses imports and `pyproject.toml` dependencies to detect frameworks you use

Example output:
```
✓ Detected source roots: src
✓ Detected test roots: tests
✓ Detected frameworks: fastapi, strawberry
```

### Customizing Configuration

Edit `lineagelens.yaml` to customize risk rules and entry points:

```yaml
source_roots: [src, lib]
test_roots: [tests]
frameworks: [fastapi, typer]

analysis:
  risk_rules:
    - category: data_write
      severity: review
      match_words: [execute, insert, update, delete]
    - category: blocking_in_async
      severity: high
      match_words: [requests., time.sleep]
      only_in_async: true

llm:
  model: gpt-4-turbo
  api_key_env: LINEAGELENS_LLM_API_KEY
```

**Key fields**:
- `source_roots` — Directories where your code lives (scanned for symbols)
- `test_roots` — Directories to mark as test code (affects entry point detection)
- `frameworks` — List of frameworks to detect for entry points (API routes, CLI commands, etc.)
- `analysis.risk_rules` — Configurable patterns for security/performance risks
- `llm` — Optional: LLM API config for generating descriptions (separate from deterministic analysis)

### Understanding Framework Configuration

The `frameworks` list controls **entry point detection**, not basic code analysis.

#### What Always Works (regardless of frameworks)

✅ Function/class definitions are found  
✅ Direct function calls are traced  
✅ Module structure is mapped  
✅ Docstrings are captured  
✅ Manual risk rules fire (e.g., "data_write", "blocking_in_async")  
✅ Impact analysis shows blast radius  
✅ Agents can still understand your code  

#### What Changes With Frameworks

**With framework listed in config**:
```yaml
frameworks: [fastapi, anthropic, langchain, boto3]
```

Entry points are detected and marked:
```json
{
  "entry_points": [
    {"symbol": "app.api.fetch_user", "kind": "api_route"},
    {"symbol": "app.llm.query", "kind": "llm_call"},
    {"symbol": "app.tasks.sync_data", "kind": "aws_call"}
  ]
}
```

Claude can answer: "Where does the LLM get called? What's the entry point for AWS operations?"

**Without frameworks** (or incomplete list):
- Same code analysis
- Same relationships discovered
- Entry points not flagged
- Harder to trace "where does this flow start?"

#### When to Add Frameworks

**Add frameworks if you want to**:
- Identify all entry points (API routes, CLI commands, scheduled tasks, LLM calls)
- Understand request flow from user interaction to database
- Find where external services are called (AWS, LLM, etc.)
- Trace which functions are "public interfaces" vs internal

**Frameworks are optional if you just need**:
- Basic code understanding and symbol relationships
- Refactoring impact analysis
- Understanding code structure without entry point mapping

#### Common Frameworks to Consider

- **Web**: `fastapi`, `flask`, `django`, `starlette`
- **CLI**: `typer`, `click`
- **LLM**: `anthropic`, `langchain`, `openai`
- **Data**: `pandas`, `sqlalchemy`, `boto3`
- **Tasks**: `celery`, `APScheduler`, `rq`
- **Validation**: `pydantic`, `marshmallow`

---

## 📚 Documentation

| Document | For | Purpose |
|----------|-----|---------|
| **README.md** (this) | Everyone | Getting started + user guide |
| **docs/claude-mcp-setup.md** | Claude Code users | Setup + tool reference |
| **docs/deploy-chatgpt-actions.md** | ChatGPT users | Deployment guide |
| **docs/CONTRIBUTING.md** | Contributors | Development setup |
| **docs/ARCHITECTURE.md** | Contributors | Technical details |
| **editors/vscode/README.md** | VS Code users | Extension usage |

---

## 🚀 Common Workflows

**Understand a new module**:
```
"Show me an overview of app.models"
→ Claude calls get_module_overview
→ You understand structure without reading files
```

**Safe refactoring**:
```
"What breaks if I remove User.find?"
→ Claude calls impact_analysis
→ You see blast radius before making changes
```

**Risk assessment**:
```
"Show me all high-severity issues"
→ Claude calls list_resiliency_risks
→ You review and prioritize fixes
```

**Onboarding new developers**:
```
"Explain the flow from /api/users to the database"
→ Claude traces full lineage
→ ~10x fewer tokens than reading raw source
```

---

## 📊 Key Benefits

✅ **Never crashes** — all errors logged, analysis continues  
✅ **Evidence-labelled** — know how reliable every signal is  
✅ **Token-efficient** — agents save ~10x tokens vs. raw source  
✅ **Safe refactoring** — impact analysis shows what breaks  
✅ **Multiple UIs** — Claude, ChatGPT, VS Code, web, CLI  
✅ **Local first** — all analysis runs on your machine  
✅ **Configurable** — customize risk rules and entry points  
✅ **Production ready** — tested on real codebases  

---

## Evidence & Trust Model — deterministic vs probabilistic

LineageLens separates **three trust tiers**. This is a promise, not a footnote:
probabilistic output is never presented as fact, and a heuristic is never passed
off as an observed runtime value.

## Evidence & Trust Model — deterministic vs probabilistic

LineageLens separates **three trust tiers**. This is a promise, not a footnote:
probabilistic output is never presented as fact, and a heuristic is never passed
off as an observed runtime value.

### 1. Deterministic — extracted facts (reproducible to the line)

Pure static-AST reads. The same source always yields the same values, and each
value traces to a file + line.

| Signal | Evidence label |
| ------ | -------------- |
| Symbol identity: kind, name, file, line, module | `static_ast` |
| Parameter names, order, defaults | `static_ast` |
| Author-written parameter and return type annotations | `annotation` |
| Decorators, async-ness | `static_ast` |
| Raw call sites: what was called, and at which line | `static_ast` |
| Relation kind (`CALLS`, `AWAIT_CALLS`, `CREATES_TASK`) | `static_ast` |
| Entry-point classification (API route, CLI command, test, framework callback) | rule-based from `lineagelens.yaml` |

### 2. Deterministic heuristics — reproducible inference (labelled as inference)

Static guesses. **Deterministic** in that the same code always produces the same
output; **approximate** in that they are not observed at runtime and are not
guaranteed correct. Whenever nothing can be inferred, the value is explicitly
`unknown` — nothing is silently guessed.

| Signal | Evidence label |
| ------ | -------------- |
| Inferred type of a return expression (`list`, `dict`, `call result`, …) | `return_expression` |
| Inferred type of an argument at a call site | `inferred_type` |
| Resolved call targets (via imports / scope walk) | `resolution: resolved` vs `external_or_dynamic` |
| Static risk signals from configurable keyword/text rules | rule match, e.g. `execute at line 12` |

> **Performance & security (static tier).** The built-in performance and
> security signals are *deterministic heuristics* — configurable keyword/pattern
> rules that fire identically on every run. They are **hints to review**, not
> measurements and not verdicts. Severity (`review`, `high`) is assigned by
> rules in `lineagelens.yaml`.

### 3. Probabilistic — LLM-generated (labelled `llm`)

Anything produced by a generative model. Output depends on the model, prompt,
and version, may differ between runs, and models can hallucinate. Probabilistic
output is stored separately and clearly tagged — it is never merged into the
deterministic graph as fact.

| Signal | Trust |
| ------ | ----- |
| Method documentation (purpose, semantics of inputs/outputs) | Probabilistic — LLM |
| LLM performance characteristics & bottleneck analysis | Probabilistic — LLM estimates, not measurements |
| LLM security & handling analysis (CWE-style review, recommendations) | Probabilistic — LLM judgement, not a security guarantee |

## Where each label lives

- `Symbol.inputs[].type`, `Symbol.outputs[].type` → value of author annotation, or `unknown`
- `Symbol.outputs[].evidence` → `annotation` | `return_expression`
- `Relation.evidence` → `static_ast`
- `Relation.resolution` → `resolved` | `external_or_dynamic`
- `Relation.arguments[].inferred_type` → `unknown` or an inferred type name
- `Symbol.risks[].evidence` → `"<raw call> at line <n>"` (rule match)
- LLM-generated documentation → `llm`

## Design rules

- Facts, heuristics, and probabilities are labelled at the point of production
  (`evidence`, `resolution`, `inferred_type`, `unknown`, `llm`).
- Uncertainty is explicit: what cannot be inferred is `unknown`, never guessed.
- Probabilistic content is opt-in (requires an LLM call) and never carries
  API credentials into artifacts.
