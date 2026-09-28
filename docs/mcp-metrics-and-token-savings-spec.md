# LineageLens Local SQLite Metrics & MCP Token Savings Reporter Spec

## 1. Executive Summary

LineageLens provides deterministic, compiler-grade code intelligence that eliminates the need for LLMs to read entire source files into their context windows. Currently, invocation metrics and token telemetry are only transmitted upstream to ContextServe.ai when a developer is authenticated with an API key. When running unauthenticated or offline, these metrics are discarded.

This specification details the architecture for:
1. **Local SQLite Metrics Persistence**: Recording all MCP tool executions, token metrics, latency, and query provenance locally into `.lineagelens/metrics.sqlite`.
2. **Ground-Truth Token Savings Calculation**: Deterministically calculating the counterfactual baseline (`raw_tokens` that an LLM agent would have spent loading full source files into context) versus the surgical LineageLens response (`optimized_tokens`).
3. **CLI Reporting Command (`lineagelens report`)**: Providing developers and engineering teams with an interactive and machine-readable (JSON) summary of MCP tool usage, net tokens saved, context reduction percentage, and estimated dollar savings.
4. **Privacy & Offline Guarantee**: 100% local, zero network egress required, zero latency overhead on tool execution.

---

## 2. Problem Statement & Motivation

### Current Limitations
- **Metrics Dropped When Unauthenticated**: In `src/lineagelens/mcp/server.py`, `_send_telemetry` checks `if not token: return`. Developers evaluating LineageLens locally via Hackernews or open-source tools have zero visibility into their actual token savings.
- **Graph Rebuild Risk**: In `src/lineagelens/store/db.py`, `GraphStore.create()` unlinks `graph.sqlite` on rebuilds (`lineagelens index . --force`). Any metrics stored in `graph.sqlite` would be erased whenever code changes are re-indexed.
- **No Local ROI Reporting**: Developers using Cursor, Claude Desktop, Antigravity, or Codex cannot inspect how many tokens LineageLens saved during their coding sessions without logging into ContextServe.ai's web dashboard.

---

## 3. Database Architecture: `.lineagelens/metrics.sqlite`

### 3.1 Separate Database Strategy
Metrics are stored in `.lineagelens/metrics.sqlite` (separate from `.lineagelens/graph.sqlite`):
- **Persistence Across Reindexes**: Code indexing runs frequently as developers write code. Metrics are an append-only time series of tool invocations that must outlive code graph rebuilds.
- **Schema Isolation**: The code graph is pinned to `SCHEMA_VERSION = 4` with strict checksum verification. Metrics have an independent, lightweight lifecycle.
- **Concurrency & WAL Mode**: MCP tools run concurrently across multiple threads/processes. WAL mode (`PRAGMA journal_mode = WAL`) guarantees non-blocking concurrent writes with negligible latency (< 0.5ms).

### 3.2 Database Schema (DDL)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS mcp_invocations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp         TEXT NOT NULL,               -- ISO-8601 UTC string (e.g. '2026-09-27T21:30:00Z')
    tool_name         TEXT NOT NULL,               -- e.g. 'find_paths', 'impact_of', 'callers_of'
    query_type        TEXT NOT NULL,               -- e.g. 'mcp_find_paths' (ContextServe.ai contract)
    raw_tokens        INTEGER NOT NULL,           -- Counterfactual baseline tokens without LineageLens
    optimized_tokens  INTEGER NOT NULL,           -- Actual serialized response tokens sent to LLM
    tokens_saved      INTEGER NOT NULL,           -- max(0, raw_tokens - optimized_tokens)
    duration_ms       INTEGER NOT NULL,           -- Execution latency in milliseconds
    response_bytes    INTEGER NOT NULL,           -- Byte size of JSON response payload
    status            TEXT NOT NULL DEFAULT 'ok',  -- 'ok' | 'error'
    error_message     TEXT,                        -- Exception message if status == 'error'
    intent            TEXT,                        -- Agent intent if specified (e.g. 'precise')
    result_count      INTEGER DEFAULT 0,           -- Number of items returned in 'results'
    truncated         INTEGER DEFAULT 0,           -- 1 if result set was capped
    repo_name         TEXT NOT NULL,               -- Basename of indexed project
    branch            TEXT,                        -- Git branch at execution time
    commit_sha        TEXT,                        -- Git commit at execution time
    model_name        TEXT NOT NULL DEFAULT 'gpt-4o', -- Target model for pricing baseline
    synced_to_cloud   INTEGER NOT NULL DEFAULT 0,  -- 1 if transmitted upstream to ContextServe.ai
    synced_at         TEXT                         -- ISO-8601 timestamp of successful cloud sync
);

CREATE INDEX IF NOT EXISTS idx_mcp_invocations_timestamp ON mcp_invocations(timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_tool ON mcp_invocations(tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_repo ON mcp_invocations(repo_name);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_sync ON mcp_invocations(synced_to_cloud);
```

---

## 4. Deterministic Token Savings Calculation

A core requirement is avoiding arbitrary or fabricated numbers. LineageLens bases its calculations on verifiable, compiler-grade facts.

### 4.1 Ground-Truth Counterfactual (`raw_tokens`)
When an AI agent uses generic file exploration instead of LineageLens, it must read entire source files into its context window to discover call chains, blast radii, or data flows.

1. **Graph-Attributed File Size Calculation**:
   - During the execution of tools such as `find_paths`, `callers_of`, `callees_of`, `impact_of`, `explain`, `dataflow_of`, and `get_symbol`, the query engine touches specific nodes.
   - LineageLens queries its own `files` table in `graph.sqlite` to find the set of distinct files containing those nodes:
     ```sql
     SELECT DISTINCT f.size_bytes 
     FROM files f
     JOIN nodes n ON n.file_id = f.id
     WHERE n.id IN (:touched_node_ids);
     ```
   - Counterfactual tokens for those source files:
     $$\text{raw\_tokens} = \sum \left\lfloor \frac{\text{file.size\_bytes}}{4} \right\rfloor$$
2. **Conservative Fallback / Baseline Floor**:
   - For tools that do not resolve specific node IDs (or return 0 results), an agent would still read at least the immediate file or run a search scan.
   - Floor:
     $$\text{raw\_tokens} = \max(\text{raw\_tokens}, \text{optimized\_tokens} \times 8, 1200)$$
     *(1,200 tokens corresponds to ~300 lines of standard source code, a conservative baseline for one source file).*

### 4.2 Optimized Tokens (`optimized_tokens`)
- Measured directly on the serialized wire payload returned to the agent:
  $$\text{optimized\_tokens} = \max\left(1, \left\lfloor \frac{\text{response\_bytes}}{4} \right\rfloor\right)$$
  *(Matches the project's canonical constant `BYTES_PER_TOKEN = 4` in `src/lineagelens/telemetry.py`).*

### 4.3 Metrics Formulas
- **Tokens Saved**: $\text{tokens\_saved} = \max(0, \text{raw\_tokens} - \text{optimized\_tokens})$
- **Context Reduction Ratio**: $\text{reduction\_pct} = \frac{\text{tokens\_saved}}{\text{raw\_tokens}} \times 100\%$
- **Estimated Cost Savings ($ USD)**:
  $$\text{cost\_saved} = \left(\frac{\text{tokens\_saved}}{1{,}000{,}000}\right) \times \text{model\_rate\_per\_1M}$$

#### Default Pricing Model Table ($ per 1M Input Tokens)
| Model Identifier | Input Rate / 1M | Output Rate / 1M | Blended Rate Used |
| :--- | :--- | :--- | :--- |
| `gpt-4o` *(default)* | $2.50 | $10.00 | **$5.00** |
| `claude-3-5-sonnet` | $3.00 | $15.00 | **$6.00** |
| `o1` / `o3-mini` | $5.00 | $20.00 | **$10.00** |
| `deepseek-r1` / `v3` | $0.55 | $2.19 | **$1.00** |

---

## 5. Execution Pipeline: Zero-Overhead Interception

### 5.1 Interceptor in `src/lineagelens/mcp/server.py`
In `create_server()`, the `@guarded` decorator intercepts every tool invocation:

```python
# Within guarded wrapper:
with telemetry.timed() as clock:
    try:
        result = await fn(*args, **kwargs)
        status = "ok"
        error_msg = None
    except Exception as exc:
        status = "error"
        error_msg = str(exc)
        ...

response_bytes = _response_bytes(result)
raw_tokens, optimized_tokens = compute_token_impact(engine, fn.__name__, result, response_bytes)

# 1. Non-blocking asynchronous / background local SQLite write:
record_local_metric(
    db_path=project / ".lineagelens" / "metrics.sqlite",
    tool_name=fn.__name__,
    raw_tokens=raw_tokens,
    optimized_tokens=optimized_tokens,
    duration_ms=clock.ms,
    response_bytes=response_bytes,
    repo_name=project.name,
    branch=branch,
    commit_sha=commit_sha,
    status=status,
    error_message=error_msg,
    intent=kwargs.get("intent"),
    result_count=len(result.get("results", ())) if isinstance(result, dict) else 0,
)

# 2. Live telemetry sync if authenticated & opted in:
if is_cloud_telemetry_enabled():
    _send_telemetry(fn.__name__, raw_tokens, optimized_tokens)
```

---

## 6. ContextServe.ai Cloud Reporting & Synchronization

Developers and organizations who want centralized visibility can report their local SQLite metrics back to **ContextServe.ai** on-demand or automatically.

### 6.1 Synchronization Mechanism
When synchronization is triggered (via `lineagelens report --sync`):
1. **Authentication Check**:
   - Checks for active session via `CredentialsStore` (from `lineagelens auth login`) or `CONTEXTSERVE_API_KEY` / `LINEAGELENS_API_TOKEN` environment variables.
   - If missing, prints clear instructions:
     ```text
     Not authenticated to ContextServe.ai.
     Run `lineagelens auth login` or set `CONTEXTSERVE_API_KEY=<token>` to sync metrics.
     ```
2. **Batch Ingestion**:
   - Queries all rows in `.lineagelens/metrics.sqlite` where `synced_to_cloud = 0`.
   - Chunks payloads in batches of up to 100 records and posts to:
     `POST /api/v1/telemetry/tokens/batch` (or sequentially to `/api/v1/telemetry/tokens`).
   - Payload matches the upstream ingestion contract:
     ```json
     {
       "events": [
         {
           "query_type": "mcp_find_paths",
           "raw_tokens": 14200,
           "optimized_tokens": 420,
           "tokens_saved": 13780,
           "duration_ms": 18,
           "repo_name": "LineageLens",
           "branch": "main",
           "commit_sha": "a1b2c3d4e5f6",
           "model_name": "gpt-4o",
           "timestamp": "2026-09-27T21:15:32Z"
         }
       ]
     }
     ```
3. **Local State Update**:
   - On HTTP 200/201 acknowledgment from the server, updates local rows:
     ```sql
     UPDATE mcp_invocations 
     SET synced_to_cloud = 1, synced_at = datetime('now') 
     WHERE id IN (:synced_ids);
     ```
   - Unsynced records remain preserved locally in SQLite for retry if network or server is unavailable.

---

## 7. CLI Command: `lineagelens report`

### 7.1 Command Specification
Add `report` as a top-level subcommand in `src/lineagelens/cli.py`:

```bash
lineagelens report [PATH] [OPTIONS]
```

#### Arguments & Options
- `PATH`: Path to project root (default: `.`)
- `--period [today|7d|30d|all]`: Filter report by time window (default: `all`)
- `--by-tool`: Display breakdown table for every individual MCP tool
- `--sync`: **Upload and report unsynced local metrics to ContextServe.ai**
- `--endpoint <url>`: Override ContextServe.ai API endpoint (defaults to active environment or `https://contextserve.ai`)
- `--json`: Output raw JSON data for CI/CD assertions and automation
- `--model [gpt-4o|claude-3-5-sonnet|deepseek-r1]`: Model pricing baseline (default: `gpt-4o`)
- `--reset`: Wipe local metrics history (prompts confirmation unless `-y` is passed)

### 7.2 Visual Output Format (Terminal UI)

```text
========================================================================================
                      LINEAGELENS MCP TOKEN SAVINGS REPORT
 Repository: LineageLens (branch: main)
 Period    : Last 7 Days (2026-09-20 to 2026-09-27)
 Target    : gpt-4o ($5.00 / 1M blended tokens)
 Cloud Sync: 142 / 142 synced to ContextServe.ai (org: ContextServe)
========================================================================================

  TOTAL INVOCATIONS        BASELINE TOKENS        OPTIMIZED TOKENS       NET TOKENS SAVED
        142                  1,842,500                 48,200               1,794,300
                     (Unindexed Context)    (LineageLens MCP)         (97.4% Reduction)

  ESTIMATED COST SAVINGS: $8.97 USD

TOOL BREAKDOWN:
Tool                  Invocations   Raw Tokens   Optimized   Tokens Saved   Reduction   Avg Latency
------------------------------------------------------------------------------------------------
find_paths                     38      542,000      12,400        529,600       97.7%          19ms
impact_of                      32      480,000       9,800        470,200       98.0%          31ms
callers_of                     28      336,000       8,400        327,600       97.5%          14ms
callees_of                     22      264,000       7,100        256,900       97.3%          12ms
dataflow_of                    12      144,000       6,200        137,800       95.7%          26ms
explain                         6       48,000       2,800         45,200       94.2%          16ms
explore                         4       28,500       1,500         27,000       94.7%          21ms
------------------------------------------------------------------------------------------------
TOTAL                         142    1,842,500      48,200      1,794,300       97.4%          20ms
========================================================================================
```

#### Sync Execution Output (`lineagelens report --sync`)
```text
Connecting to ContextServe.ai (environment: production)...
Found 48 unsynced invocation metrics.
Uploading batch 1/1 (48 records)... [OK]
Successfully reported 48 metrics to https://contextserve.ai (repo: LineageLens, org: ContextServe).
```

### 7.3 Machine-Readable JSON Output (`--json`)

```json
{
  "repo_name": "LineageLens",
  "branch": "main",
  "period": "7d",
  "cloud_sync": {
    "total": 142,
    "synced": 142,
    "pending": 0,
    "last_synced_at": "2026-09-27T21:18:02Z"
  },
  "summary": {
    "total_invocations": 142,
    "raw_tokens": 1842500,
    "optimized_tokens": 48200,
    "tokens_saved": 1794300,
    "reduction_percentage": 97.38,
    "cost_savings_usd": 8.97,
    "model_name": "gpt-4o",
    "avg_duration_ms": 20.1
  },
  "by_tool": {
    "find_paths": {
      "calls": 38,
      "raw_tokens": 542000,
      "optimized_tokens": 12400,
      "tokens_saved": 529600,
      "reduction_percentage": 97.71,
      "avg_duration_ms": 19.4
    }
  }
}
```

---

## 8. Implementation Plan

| Step | Component | Description |
| :--- | :--- | :--- |
| **1** | `src/lineagelens/store/metrics_store.py` | Create SQLite store for `.lineagelens/metrics.sqlite`, schema migrations, record insert, query aggregations. |
| **2** | `src/lineagelens/mcp/server.py` | Update `@guarded` decorator to calculate ground-truth `raw_tokens` from files touched and log to `MetricsStore`. |
| **3** | `src/lineagelens/sync.py` | Implement batch sync engine to report unsynced SQLite records upstream to `ContextServe.ai`. |
| **4** | `src/lineagelens/cli.py` & `report.py` | Add `lineagelens report` subcommand supporting `--period`, `--by-tool`, `--sync`, `--json`, `--model`, and `--reset`. |
| **5** | `tests/test_metrics_store.py` | Comprehensive test coverage: table creation, concurrent writes, time period filters, and token calculation accuracy. |
| **6** | `tests/test_sync.py` | Test batch upload to mock ContextServe HTTP endpoint and ensure `synced_to_cloud` transitions. |
| **7** | Documentation Updates | Document `lineagelens report` and `--sync` in `README.md`, `docs/TELEMETRY.md`, and ContextServe Docs component. |

---

## 9. Verification & Acceptance Criteria

1. **Offline & Non-Interfering**: Calling MCP tools with no network and no API key correctly logs rows to `.lineagelens/metrics.sqlite` with `synced_to_cloud = 0` without failing or slowing down the tool return.
2. **Re-index Safety**: Running `lineagelens index . --force` purges and rebuilds `graph.sqlite` while leaving `.lineagelens/metrics.sqlite` intact.
3. **Accuracy Verification**: `tokens_saved` matches `raw_tokens - optimized_tokens` across all queries; reduction percentage is mathematically verified.
4. **Cloud Reporting & Sync**: Running `lineagelens report --sync` pushes all pending records to ContextServe.ai and marks them `synced_to_cloud = 1` and `synced_at = <timestamp>`.
5. **CLI Formats**: `lineagelens report` renders the formatted terminal table; `lineagelens report --json` outputs valid JSON parseable by `jq`.
