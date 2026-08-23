# Architecture & Technical Details

For contributors and maintainers understanding the LineageLens codebase.

## Overview

LineageLens is a Python code analysis tool with three API surfaces (GraphQL, REST, MCP) built on a single query layer that operates on an immutable code graph model.

```
Analyzer (safe AST parsing)
    ↓
CodeGraph (symbols + containers + relations + evidence)
    ↓
queries.py (single source of truth for all operations)
    ↓
  / | \
GraphQL REST MCP
  |   |   |
 Web ChatGPT Claude
      Actions Code
```

## Core Components

### 1. Analyzer (`src/lineagelens/analyzer.py`)

**Responsibility**: Parse Python source files and extract code structure.

**Design**:
- Static only, and it never imports or executes the target project. Type
  inference goes through Jedi, which parses with `parso` rather than importing,
  so the safety guarantee holds.
- Two-pass algorithm: Definitions (collect symbols) + Relationships (collect relations)
- Resilient: catches all errors per-file, continues analysis
- Reports: `AnalysisReport` tracks failures, warnings, success metrics

**Key Classes**:
- `Module`: Represents one `.py` file
- `Definitions`: AST visitor that collects all symbols (functions, classes)
- `Relationships`: AST visitor that collects all relations (calls, risks, entry points)

**Functions**:
- `analyze(root, config)` → `(CodeGraph, AnalysisReport)` - main entry point
- Helper functions: `dotted()`, `expression()`, `infer()` - AST utilities

**Important Decision**: separate passes let the relationships visitor assume
every symbol already exists, so it never has to guard a lookup.

The pipeline is now five phases, not two:

1. **Definitions** — parse each file, emit `Symbol`s and `Container`s, plus one
   synthetic `<module>` symbol per module to own statements outside any function.
   Without it every module-level call site was discarded, which made all
   framework wiring invisible.
2. **Attribute hoisting** — map `self.attr` to a class id, so `self.db.query()`
   resolves. Recognises annotations, constructor calls, annotated factories, and
   annotated-parameter passthrough (`def __init__(self, db: Database): self.db = db`).
3. **Relationships** — emit relations. Calls plus the reference kinds:
   `REFERENCES`, `ANNOTATES`, `INHERITS`, `DECORATES`, `EXPORTS`, `IMPORTS`.
   Cheap static resolution runs first and type inference is a gated fallback.
4. **Whole-graph passes** — `OVERRIDES` (needs every `INHERITS` edge),
   `USES_FIXTURE`, and project-level entry points (`__main__` guards,
   `[project.scripts]`).
5. **Reachability** (on demand, cached) — walk from entry points and assign a
   verdict plus rescue mechanism per symbol.

### Relation kinds

Reachability in Python does not flow through calls alone, so the graph models
more than calls. Each kind is switchable via `analysis.relation_kinds`.

| kind | source → target | tier |
|---|---|---|
| `CALLS` / `AWAIT_CALLS` / `CREATES_TASK` | caller → callee | fact; heuristic when the target came from inference |
| `REFERENCES` | scope → a name loaded as a value | fact |
| `ANNOTATES` | annotated symbol → class | fact |
| `INHERITS` | subclass → base | fact |
| `OVERRIDES` | override → base method | **heuristic** — Python has no `override` keyword |
| `DECORATES` | decorated → decorator | fact |
| `EXPORTS` | module scope → `__all__` entry | fact |
| `IMPORTS` | module scope → module scope | fact |
| `USES_FIXTURE` | test → fixture | **heuristic** — a name match |
| `REFERENCES_STRING` | scope → symbol named in a string | **heuristic**, off by default |

`REFERENCES` is the one with volume risk: unfiltered it emits 27,522 edges on a
real project, more than twice the call count. Two rules keep it near 1,400 and
are load-bearing, not optimisations — emit only when the name resolves to an
in-repo `Symbol`, and consider only the outermost node of an attribute chain.

### New modules

| module | responsibility |
|---|---|
| `index.py` | `GraphIndex`: cached adjacency and lookup tables, plus an mtime-keyed loader so a server parses `graph.json` once rather than per request |
| `entrypoints.py` | entry-point rules — the roots of the reachability walk |
| `reachability.py` | the walk, the verdict vocabulary, and rescue mechanisms |
| `ratchet.py` | dead-code baseline comparison for CI |
| `hooks.py` | git hook installation |

### Schema version

`graph.json` carries `schema_version`, and `load_graph` refuses a mismatch rather
than serving symbol ids that mean something different from what the reader
expects.

---

### 2. Model (`src/lineagelens/model.py`)

**Responsibility**: Define canonical data structures for code graphs.

**Key Classes**:
- `Evidence`: Typed evidence model (deterministic_fact | heuristic | probabilistic)
- `Symbol`: Represents a function, class, or module
  - `id`, `kind`, `name`, `file`, `line`, `end_line`
  - `parent`: enclosing module/class (for compound nodes)
  - `description`: docstring (deterministic fact)
  - `inputs`/`outputs`: contract specification
  - `resiliency`: list of `ResiliencySignal` objects
- `Container`: Package or module node (for hierarchical visualization)
- `Relation`: Function call relationship
- `CodeGraph`: Collection of symbols, containers, relations

**Important Design**: All data is immutable after creation. Modifications (like adding resiliency signals) happen during analyzer passes, not afterward.

---

### 3. Configuration (`src/lineagelens/config.py`)

**Responsibility**: Load and manage user configuration from `lineagelens.yaml`.

**Key Classes**:
- `ProjectConfig`: Root configuration (source_roots, frameworks, etc.)
- `AnalysisConfig`: Risk rules and entry point patterns
- `RiskRule`: Configurable risk detection rule
- `LLMConfig`: Optional LLM enrichment configuration

**Design Pattern**: All config is frozen (immutable) for thread safety. Defaults are sensible and complete.

---

### 4. Queries (`src/lineagelens/queries.py`)

**Responsibility**: Single source of truth for all graph operations.

**Key Functions**:
- `load_graph()`, `load_report()` - I/O
- `get_symbol()`, `search_symbols()` - lookups
- `get_callers()`, `get_callees()` - direct relationships
- `get_lineage()` - transitive call paths (BFS)
- `impact_analysis()` - backward closure (what breaks if I change this?)
- `get_module_overview()` - shallow module summary
- `list_entry_points()`, `list_resiliency_risks()` - filtered lists

**Design**: Pure functions taking `graph` parameter. No global state. Every operation is reusable by GraphQL, REST, and MCP.

**Critical Advantage**: Changes to query logic only need to be made once. All three API surfaces automatically get the fix.

---

### 5. REST API (`src/lineagelens/rest.py`)

**Responsibility**: Expose queries as HTTP endpoints for web UI and ChatGPT Actions.

**Endpoints**: 11 total
- `GET /api/v1/symbols/{id}` - full symbol detail
- `GET /api/v1/search?text=...` - search
- `GET /api/v1/graph/view?module=...` - for visualization (with module filtering for lazy loading)
- `GET /api/v1/symbols/{id}/impact` - impact analysis
- `GET /api/v1/entry-points` - list entry points
- etc.

**Response Models**: Pydantic models (`SymbolOut`, `RelationOut`, etc.) auto-generate OpenAPI schema for ChatGPT Actions integration.

**Design**: Thin adapter layer. Core logic lives in `queries.py`, not here. Every endpoint calls a queries function.

---

### 6. GraphQL (`src/lineagelens/web.py`)

**Responsibility**: Expose queries as GraphQL for backwards compatibility and optional usage.

**Schema**: Strawberry types wrapping Pydantic models from REST layer.

**Queries**:
- `symbol(id)` - get symbol
- `search(text, limit)` - search
- `callers(id)` - callers
- `callees(id)` - callees

**Design Note**: GraphQL was the original interface. REST was added for ChatGPT. Going forward, GraphQL is optional; REST is primary.

---

### 7. MCP Server (`src/lineagelens/mcp_server.py`)

**Responsibility**: Expose queries as Model Context Protocol tools for Claude Code.

**Tools**: the `queries.py` functions wrapped for MCP, plus `list_dead_code`
and `get_reachability` over the reachability model.

**Design**:
- Uses FastMCP for easy tool registration
- Each tool takes `project` as explicit parameter (supports multiple projects)
- Mtime-based graph caching (avoid reparsing on every call)
- Stdio transport (standard MCP protocol)

**Key Design**: Tools serialize output with `asdict()` for JSON compatibility.

---

### 8. Report & Enrichment

**Report** (`src/lineagelens/report.py`):
- Tracks analysis failures (`FileFailure`), warnings (`SymbolWarning`)
- Provides human-readable summary
- Generated during analysis phase

**Enrichment** (`src/lineagelens/enrich.py`):
- Separate, opt-in LLM enrichment
- Writes to `.lineagelens/enrichment.json` (separate from `graph.json`)
- Never merged into deterministic graph
- Satisfies "Tier 3 probabilistic is always separate" requirement

---

## Data Flow

### Analysis Phase
```
Python files
  ↓
AST parsing
  ↓
Definitions pass: extract symbols
  ↓
Relationships pass: extract relations + risks + entry points
  ↓
CodeGraph + AnalysisReport
  ↓
Write .lineagelens/graph.json + .lineagelens/report.json
```

### Query Phase
```
Client (Claude / ChatGPT / REST / GraphQL)
  ↓
REST endpoint / GraphQL query / MCP tool call
  ↓
queries.py function
  ↓
CodeGraph (in-memory, loaded once)
  ↓
Response (JSON / GraphQL / MCP)
```

### Enrichment Phase (Optional)
```
User runs: lineagelens enrich .
  ↓
LLM generates descriptions for symbols
  ↓
Write .lineagelens/enrichment.json
  ↓
On next query, optionally merge enrichment data (query-time, not write-time)
```

## Testing Strategy

### Unit Tests

**`tests/test_analyzer.py`**: Analyzer correctness
- Entry point detection
- Config-driven risk rules
- Config roundtrip

**`tests/test_queries.py`**: Query correctness
- Fixture graph with known call patterns
- Each query function tested independently
- Multi-hop lineage accuracy
- Impact analysis coverage

### Integration Tests

Run LineageLens on itself:
```bash
lineagelens analyze .
# Self-analysis is a smoke test, not a fixture. The real regression gate is
# tests/fixtures/reachability_corpus + EXPECTED.yaml, which pins a verdict and a
# rescue mechanism for every symbol in a project built to exercise each one.
```

### Smoke Tests

- CLI: `lineagelens analyze`, `lineagelens serve`
- REST: `curl http://localhost:8717/api/v1/entry-points`
- GraphQL: Query via `/graphql` endpoint
- MCP: `lineagelens-mcp` launches and responds to tool calls

---

## Performance Characteristics

### Analysis
- **Speed**: ~50ms per 1K LOC (single-threaded)
- **Memory**: ~100KB per 1K LOC in graph.json
- **I/O**: One read pass over all Python files + two AST walks

### Queries
- **Speed**: < 1ms for most queries (in-memory graph)
- **Memory**: Graph stays in-memory for query session
- **Caching**: MCP server caches graph by mtime (avoid re-parsing)

### Scaling
- Exercised against a real 152-file / 1,772-symbol / 13,958-relation project
- Estimated good up to 10K LOC on modern machines
- For very large codebases: module-scoped queries recommended

---

## Error Handling Strategy

**Philosophy**: Never crash analysis. Log everything.

**Implementation**:
1. Per-file try/except in analyzer:
   - Read error → `FileFailure` + continue
   - Parse error → `FileFailure` + continue
   - Visitor error → `FileFailure` + continue

2. Guarded symbol access in Relationships visitor:
   - Symbol not found → `SymbolWarning` + skip mutation

3. Query errors:
   - File not found → raise `GraphNotFoundError` (user's responsibility)
   - Query logic errors → return empty result (graceful degradation)

**Result**: `report.json` provides clear visibility into what succeeded/failed.

---

## Design Decisions

### Why Two-Pass Analysis?
- **Pass 1 (Definitions)**: Collect all symbols deterministically
- **Pass 2 (Relationships)**: Walk relationships, confident symbols exist
- **Benefit**: Avoids KeyError crashes if Pass 1 and Pass 2 diverge

### Why Separate Enrichment?
- **Deterministic graph** (graph.json): Facts and heuristics only
- **Probabilistic enrichment** (enrichment.json): LLM output only
- **Benefit**: Clear separation of concerns, auditability, opt-in nature

### Why Single Query Layer?
- **One source of truth**: Bug fix in `get_lineage()` fixes all three APIs
- **Consistency**: GraphQL, REST, MCP return identical results
- **Maintainability**: New query? Add one function to queries.py, all APIs get it

### Why Module Scoping in REST API?
- **Large repos**: Loading 10K+ symbols at once is slow
- **Lazy loading**: Frontend requests `/graph/view?module=app.api`
- **Benefit**: Handles large codebases gracefully

---

## Contributing Guidelines

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and PR process.

When adding new features:
1. Add query function to `queries.py` first
2. Add REST endpoint
3. Add GraphQL query
4. Add MCP tool
5. Add tests for each

This ensures consistency across all interfaces.

---

## Future Improvements

- [ ] Dynamic import resolution (ast.FunctionDef.body analysis)
- [ ] Type annotation inference from assignments
- [ ] Circular import detection
- [ ] Unused code detection
- [ ] Database schema inference
- [ ] Async pattern detection improvements
- [ ] Performance: parallel file parsing
- [ ] Incremental analysis (re-analyze only changed files)

See GitHub Issues for detailed discussion on each.
