# LineageLens

> **Real-Time Code Lineage & Visual Analytics**  
> A deterministic code graph for coding agents. Control flow, data flow, and cross-service contracts across seven languages, in one SQLite file.

The problem it solves: an agent asked to fix a bug or plan a feature reads whole files to reconstruct what calls what, where a value comes from, and what a change will break. That is slow, expensive, and gets worse as the repository grows. LineageLens answers those questions directly — with the source you need and an honest statement of what it could not determine.

> **100% Local & Offline**: LineageLens requires no account, no login, and no API key. All indexing, call graph generation, and MCP queries run entirely inside your local machine in SQLite (`.lineagelens/graph.db`). Zero telemetry by default.

---

## ⚡ Quick Start

### 1. Recommended Setup: Docker Compose Sidecar (Cursor & VS Code)

Keep a warm LineageLens container running in the background for sub-millisecond MCP query latency without polluting your host toolchain:

Create `docker-compose.mcp.yml` in your project root:

```yaml
services:
  lineagelens:
    image: ghcr.io/contextserve/lineagelens:latest
    container_name: lineagelens
    restart: unless-stopped
    working_dir: /workspace
    entrypoint: sleep
    command: infinity
    volumes:
      - .:/workspace
    # environment:
    #   - LINEAGELENS_API_TOKEN=${LINEAGELENS_API_TOKEN}
```

```bash
# Start the persistent MCP sidecar
docker compose -f docker-compose.mcp.yml up -d

# Index your project instantly inside the running sidecar
docker exec lineagelens lineagelens index .

# Query the graph
docker exec lineagelens lineagelens query . impact src/orders/service.py:412
```

---

### Alternative: Single Docker Run Sidecar

```bash
# Start container in detached mode (persistent execution sidecar)
docker run --rm -d --name lineagelens \
  -v $(pwd):/workspace \
  -w /workspace \
  --entrypoint sleep \
  ghcr.io/contextserve/lineagelens:latest infinity
```

---

### 2. Shell Alias (Feels 100% Native)

Add this alias to your shell profile (`~/.zshrc` or `~/.bashrc`) to run LineageLens with native CLI speed:

```bash
alias lineagelens='docker exec lineagelens lineagelens'
```

```bash
# Reload profile
source ~/.zshrc

# Now run commands directly:
lineagelens index .
lineagelens query . impact src/orders/service.py:412
lineagelens coverage .
```

---

### 3. One-Off Execution (Ephemeral `docker run`)

Run without keeping a container running in the background:

```bash
# One-off index
docker run --rm -v $(pwd):/workspace -w /workspace ghcr.io/contextserve/lineagelens:latest lineagelens index .

# One-off query
docker run --rm -v $(pwd):/workspace -w /workspace ghcr.io/contextserve/lineagelens:latest lineagelens query . impact src/orders/service.py:412
```

---

### 4. Native Installation (Python 3.10+)

```bash
pip install lineagelens
lineagelens index .
lineagelens query . impact src/orders/service.py:412
```

---

### Available Docker Images

| Tag / Target | Runtimes Included | Best For |
|---|---|---|
| `:latest` / `:monolith` | All 7 runtimes + all SCIP indexers | Multi-language & polyglot codebases |
| `:node` | Python + Node.js LTS & scip-typescript | TypeScript and JavaScript projects |
| `:java` | Python + OpenJDK 17 & scip-java | Java, Kotlin, Scala / Maven & Gradle |
| `:go` | Python + Go runtime & scip-go | Go services & modules |
| `:rust` | Python + Rust & rust-analyzer | Rust crates & workspaces |
| `:clang` | Python + LLVM / Clang & scip-clang | C and C++ projects |
| `:ruby` | Python + Ruby & scip-ruby | Ruby and Rails projects |

---

## 🔍 What It Answers

| Question | Command |
|---|---|
| What does changing this line break? | `query . impact src/svc.py:412` |
| How does X reach Y? | `query . paths handleOrder saveToDb` |
| Who calls this, and how? | `query . callers submit` |
| Where does this value come from? | `query . dataflow Order.total` |
| Which service calls this route? | `query . contracts` |
| How do I add a feature like this one? | `query . similar create_order` |
| What does this stack trace touch? | `query . stacktrace error.log` |
| What does the index *not* cover? | `coverage` |

Every answer carries a **coverage envelope**: which boundaries the walk crossed, which files were skipped, and whether data flow was computed or deferred. A negative answer tells you whether it means "nothing calls this" or "we could not see".

---

## 🌐 Cross-Service Flow

The capability traditional call graphs lack. A TypeScript `fetch` and a Python route handler never reference each other directly — but both name the same route, so both attach to one contract node:

```
web/client.ts   placeOrder ──CONSUMES──▶ POST /api/orders ◀──EXPOSES── create_order   api/routes.py
```

```console
$ lineagelens query . contracts
contract_map  1 of 1

  contract:      POST /api/orders
  exposed_by:    shop-api/python/api/routes#create_order   (api/routes.py:5)
  consumed_by:   shop-web/typescript/client#placeOrder     (web/client.ts:1)
  cross_service: true
```

Thirty framework adapters ship out of the box: FastAPI, Flask, Django, Spring, Express, NestJS, ASP.NET, Go mux, fetch/axios/requests/RestTemplate, Dubbo `@DubboService`/`@DubboReference`, gRPC, Kafka, Celery, `click`, env vars, and Java SPI (`META-INF/services`).

**Unmodelled frameworks are reported, not ignored.** Anything route-shaped that no adapter claims comes back as a ranked work list:

```
frameworks present but unmodelled (no contract adapter):
  route    47 uses   e.g. api/legacy.py:12  "/api/legacy/{id}"
  add an adapter under .lineagelens/adapters/ to link these
```

Adapters are simple YAML files, so adding your in-house framework does not require patching the package.

---

## 🎯 Line-Level Impact

```console
$ lineagelens query . impact api/routes.py:4
target       : shop-api/python/api/routes#create_order
change_kind  : contract_key
operations   : EXPOSES -> POST /api/orders
cross_service: placeOrder  via POST /api/orders
```

`change_kind` matters because blast radii differ by orders of magnitude:
- A **body** change is local.
- A **signature** change breaks every caller.
- A string literal that happens to be a route key is a **contract** change with cross-service reach.

LineageLens separates in-process consequences (a build or test failure, one deploy) from cross-service ones (wire compatibility and deploy ordering).

---

## 🌊 Data Flow

Follows `READS`, `WRITES`, `PARAM_BINDS`, and `RETURNS`, so a walk crosses call boundaries instead of stopping at them:

```console
$ lineagelens query . dataflow submit/amount
  downstream  PARAM_BINDS   amount -> save/amount
  upstream    READS         amount -> submit
```

Scope is deterministic: intraprocedural def-use, positional argument binding, field read/write, and return flow. Where a chain needs alias analysis or dynamic dispatch, it reports a **boundary with its candidate set** rather than guessing.

---

## 💡 Intent: Pay for the Detail You Need

A bug fix needs line-exact detail. A feature plan does not.

```bash
lineagelens query . impact svc.py:412 --intent precise   # spans, data flow, source
lineagelens query . impact OrderService --intent plan     # shape only, ~10x cheaper
```

`plan` computes no data flow and returns no source; the envelope says so. This is the main cost control: savings come from *not computing* detail the question skips — never from truncating detail it needed.

---

## 🤖 MCP Integration & Real-Time IDE Setup

LineageLens exposes 15 tools via the Model Context Protocol (MCP). Agents query graph structure instead of reading raw files — typically reducing context consumption by 10×.

### Claude / Claude Code

Configure `.claude/settings.json` or Claude Desktop:

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-v",
        "${workspaceFolder}:/workspace",
        "-w",
        "/workspace",
        "ghcr.io/contextserve/lineagelens:latest",
        "lineagelens",
        "mcp",
        "."
      ]
    }
  }
}
```

### Real-Time IDE Setup (Cursor & VS Code)

#### Step 1: Run Persistent Sidecar in Docker

```bash
docker run --rm -d --name lineagelens \
  -v $(pwd):/workspace \
  -w /workspace \
  --entrypoint sleep \
  ghcr.io/contextserve/lineagelens:latest infinity
```

#### Step 2: Configure IDE with `mcp.json`

Add the server to `.cursor/mcp.json` (Cursor) or `.vscode/mcp.json` (VS Code):

**Option A: Recommended — Connect to Running Sidecar (`docker exec`)**

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "lineagelens",
        "lineagelens",
        "mcp",
        "/workspace"
      ]
    }
  }
}
```

> **Note for VS Code**: VS Code native MCP in `.vscode/mcp.json` uses the top-level key `"servers"` (e.g. `{"servers": {"lineagelens": {...}}}`), whereas Claude Desktop and Cursor use `"mcpServers"`.

**Option B: On-Demand Docker Execution (`docker run`)**

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-v",
        "${workspaceFolder}:/workspace",
        "-w",
        "/workspace",
        "ghcr.io/contextserve/lineagelens:latest",
        "lineagelens",
        "mcp",
        "/workspace"
      ]
    }
  }
}
```

**Option C: Native Host CLI**

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "lineagelens-mcp",
      "env": {
        "LINEAGELENS_PROJECT": "${workspaceFolder}"
      }
    }
  }
}
```

#### Step 3: Run the Real-Time Watcher

Keep the graph continuously synchronized as you edit code:

```bash
# Run the watcher inside the running MCP container
docker exec -it lineagelens python -c "from lineagelens.watcher import IndexWatcher; IndexWatcher('.').run()"

# Or run as a standalone detached watcher container
docker run --rm -d --name lineagelens-watcher \
  -v $(pwd):/workspace \
  -w /workspace \
  ghcr.io/contextserve/lineagelens:latest \
  python -c "from lineagelens.watcher import IndexWatcher; IndexWatcher('.').run()"
```

---

### MCP Tools Reference

| Tool | Description |
|---|---|
| `find_paths` | All execution paths between two symbols. |
| `trace_flow` | Trace a value through call boundaries. |
| `callers_of` | Transitive callers of a symbol (with depth cap). |
| `callees_of` | Transitive callees of a symbol. |
| `impact_of` | Blast radius of changing a symbol or line. ⭐ Most useful. |
| `impact_of_diff` | Blast radius from a diff/patch, not a single symbol. |
| `dataflow_of` | Upstream + downstream data flow for a value. |
| `similar_flows` | Find structurally similar code patterns. |
| `contract_map` | All cross-service contracts (HTTP, RPC, Kafka, etc.). |
| `list_entry_points` | All API routes, CLI commands, and event consumers. |
| `search` | Fuzzy symbol search across the codebase. |
| `get_symbol` | Full metadata for one symbol. |
| `explain` | Compiler evidence for an edge (why two components connect). |
| `map_stacktrace` | Resolve a crash log or stack trace to graph nodes. |
| `get_ontology` | Active capabilities and index levels for this install. |

Responses carry verbatim line-numbered source at `intent=precise`, so there is usually no follow-up file read. Every response is budgeted and reports truncation explicitly.

---

## 📊 Local Token Savings & MCP Metrics (ROI Reporter)

Every MCP tool invocation computes the ground-truth counterfactual baseline (source tokens an LLM would have read without LineageLens) versus the optimized response payload, saving metrics locally to `.lineagelens/metrics.sqlite`:

```bash
lineagelens report                  # Net tokens saved, context reduction %, and ROI
lineagelens report --by-tool        # Tool-by-tool breakdown
lineagelens report --period 7d      # Filter by time window (today, 7d, 30d, all)
lineagelens report --model o1       # Price against target LLMs (gpt-4o, claude-3-5-sonnet, deepseek-r1, o1, o3-mini)
lineagelens report --sync           # Sync metrics to ContextServe.ai dashboard
lineagelens report --json           # Machine-readable JSON output
```

### Deterministic Counterfactual Calculation

1. **Baseline**: For each tool call, LineageLens calculates the exact tokens in the full files that an agent would have needed to read sequentially to answer the question without a graph.
2. **Actual**: Measures the compact JSON payload returned by LineageLens.
3. **Net Saved**: $\text{Baseline Tokens} - \text{Actual Tokens}$.
4. **ROI**: Value calculated using live input/output token pricing for selected LLMs.

---

## 🛠️ CLI Commands

```
lineagelens index [path]      build the graph (all languages, one graph)
  --require-tier-b[=LANGS]    skip languages with no type resolver (CI strictness)
  --require-level=SPEC        skip languages below a capability level: L2, or
                              kotlin:L0,java:L2
  --scip[=PATH]               resolve through a SCIP index (compiler-grade)
  --upload                    publish the graph to ContextServe (opt-in)
  --upload-required           make a publish failure fatal
  --dry-run                   print the exact payload, send nothing

lineagelens query [path] ...  the twelve primitives; --json for machine output
  impact <file:line|symbol>   blast radius analysis
  paths <from> <to>           execution paths
  callers <symbol>            transitive callers
  callees <symbol>            transitive callees
  dataflow <symbol|value>     upstream/downstream value flow
  contracts                   cross-service HTTP/RPC contract map
  similar <symbol>            structurally similar flows
  stacktrace <file>           map stack trace to graph
  search <name>               fuzzy symbol search
  get_symbol <id>             symbol metadata
  explain <from> <to>         compiler evidence for an edge
  map_stacktrace <trace>      resolve crash log

lineagelens coverage [path]   what the index does and does not cover
lineagelens verify [path]     rebuild twice, confirm identical output
lineagelens ontology [path]   measured capability for this installation
lineagelens mcp [path]        run the MCP server over stdio
lineagelens serve [path]      HTTP surface for the dashboard (loopback default)
lineagelens report [path]     local SQLite token savings & MCP ROI reporter
lineagelens telemetry ...      enable, disable, status -- off until you enable it
lineagelens auth ...          login, logout, status, token, switch-env
```

---

## 🌐 Languages & Capability Levels

Breadth and depth are independent axes. A language enters at a **level** derived from what extraction specs loaded, never declared:

| Level | Delivers | Languages |
|---|---|---|
| **L2** flow | Data flow, contracts, call graph, symbol inventory | Python, Java, TypeScript, JavaScript, Go, Rust, C# |
| **L1** graph | Call graph, `impact_of`, `callers_of` | — |
| **L0** inventory | Symbols, `search`, `get_symbol` | C, Ruby, Bash, Kotlin |

### Feature Matrix

| Language | Nodes | Calls | Inherits | Implements | Data Flow | Contracts |
|---|---|---|---|---|---|---|
| **Python** | ✓ | ✓ | ✓ | n/a | ✓ | ✓ |
| **TypeScript / TSX** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **JavaScript** | ✓ | ✓ | ✓ | n/a | ✓ | ✓ |
| **Java** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **Go** | ✓ | ✓ | ✓ | partial | ✓ | — |
| **Rust** | ✓ | ✓ | n/a | partial | ✓ | — |
| **C#** | ✓ | ✓ | ✓ | ✓ | ✓ | — |

*Generated by the conformance suite. Go satisfies interfaces structurally and Rust has blanket impls, so both report `partial`.*

The core seven install by default. Breadth grammars are available via extras:

```bash
pip install 'lineagelens[wave1]'    # c, ruby, bash, kotlin
pip install 'lineagelens[all]'
```

A file whose grammar is not installed is skipped with `skip_reason = 'missing_grammar'` and reported in the coverage envelope — never silently ignored. Adding a new language is documented in [docs/ADDING-A-LANGUAGE.md](docs/ADDING-A-LANGUAGE.md).

---

## 🔬 Compiler-Grade Resolution (SCIP)

- **Tier A**: Tree-sitter AST queries (always active).
- **Tier B**: Type resolvers (Python uses vendored `jedi`; Java, Go, Rust, and C# use detected toolchains).
- **SCIP**: Compiler-verified facts from external SCIP indexers (`scip-java`, `scip-typescript`, `scip-python`, `scip-go`). Outranks static-analysis inferences.

```bash
# Generate SCIP index and build graph
scip-java index                     # or scip-typescript / scip-python / scip-go
lineagelens index --scip            # discovers ./index.scip
lineagelens index --scip=path.scip  # or specify an explicit path

# Strict mode for CI: skip or fail on languages without Tier B/SCIP
lineagelens index . --require-tier-b
```

- **LineageLens never runs indexers for you**: Doing so would make indexing non-deterministic and break `verify --determinism`.
- **Stale index protection**: Each SCIP document's text is hashed against disk; any file that differs falls back to Tier A with the count reported.

---

## 🔒 Honesty by Construction

Four invariant properties, each enforced by tests:

1. **Never guesses**: When multiple candidates match a reference, no edge is emitted and the ambiguity is recorded with its candidate set.
2. **Never silently degrades**: A missing grammar or type resolver is explicitly reported in the coverage envelope.
3. **Never loses a reference**: Every observed reference becomes an edge or an `unresolved_refs` row, and per-file totals must balance by database constraint.
4. **Reproducible**: `lineagelens verify` builds twice and compares digests. Same commit = same graph.

---

## ☁️ Cloud Sync & Team Publishing (Optional)

Indexing and querying are 100% offline. Publishing is strictly opt-in:

```bash
# 1. Login or provide token via environment variable
lineagelens auth login
# Or: export LINEAGELENS_API_TOKEN="ll_sk_..."

# 2. Build and publish
lineagelens index --upload

# 3. For CI/CD (fail build if upload fails)
lineagelens index --upload --upload-required
```

### Privacy & Zero Source Code Leakage

- **Inspect payload before sending**: `--dry-run` prints the exact bytes to stdout:
  ```bash
  lineagelens index --dry-run | jq '.graph_data | keys'
  ```
- **Source text is never uploaded**: Nodes carry only signatures and docstrings. Verbatim source lines in `usage_sites` are excluded via assertions in `src/lineagelens/upload.py` that raise rather than silently strip.
- **Failed uploads do not fail local builds**: Offline or expired tokens print a diagnostic and exit 0 unless `--upload-required` is set.

---

## 🐳 Docker Sandbox & Distribution

The `Dockerfile` uses a multi-stage architecture providing isolated environments with clean APT caches:

### Multi-Stage Build Targets

```bash
# Build language-specific slim image
docker build --target node -t ghcr.io/contextserve/lineagelens:node .
docker build --target java -t ghcr.io/contextserve/lineagelens:java .
docker build --target go -t ghcr.io/contextserve/lineagelens:go .

# Build all-in-one monolith image
docker build --target monolith -t ghcr.io/contextserve/lineagelens:latest .
```

### Multi-Language Polyglot Pipelines

```bash
# Pattern 1: Monolith (one container, all languages)
docker run --rm -v $(pwd):/workspace -w /workspace \
  ghcr.io/contextserve/lineagelens:latest \
  bash -c "scip-java index && npm install && scip-typescript index && lineagelens index ."

# Pattern 2: Multi-stage pipeline (shared volume)
docker run --rm -v my-ws:/workspace -w /workspace \
  ghcr.io/contextserve/lineagelens:node \
  bash -c "npm install && scip-typescript index -o index-ts.scip"

docker run --rm -v my-ws:/workspace -w /workspace \
  ghcr.io/contextserve/lineagelens:java \
  bash -c "scip-java index -o index-java.scip"

docker run --rm -v my-ws:/workspace -w /workspace \
  ghcr.io/contextserve/lineagelens:latest \
  lineagelens index .
```

### Enterprise Edge Cases

```bash
# Shadow node_modules for cross-OS native binary compatibility
docker run --rm \
  -v $(pwd):/workspace \
  -v /workspace/node_modules \
  -w /workspace \
  ghcr.io/contextserve/lineagelens:node \
  bash -c "npm install && scip-typescript index && lineagelens index ."

# Mount private npm / SSH keys for registry access
docker run --rm \
  -v $(pwd):/workspace \
  -v ~/.npmrc:/root/.npmrc:ro \
  -v ~/.ssh:/root/.ssh:ro \
  -w /workspace \
  ghcr.io/contextserve/lineagelens:node \
  bash -c "npm install && scip-typescript index"
```

---

## 🛡️ Telemetry

Off by default. `DO_NOT_TRACK` and `CI` environment variables suppress it permanently.

```bash
lineagelens telemetry status     # Inspect the exact event
lineagelens telemetry enable     # Opt in
lineagelens telemetry disable    # Opt out
```

Anonymous events contain only counts, execution duration, and OS versions — never a path, symbol, repository name, branch, or line of code. Full specifications are documented in [docs/TELEMETRY.md](docs/TELEMETRY.md).

---

## 📈 Scale

Benchmark on Apache Dubbo (4,046 Java files, 120 Maven modules):

| Metric | Result |
|---|---|
| **Nodes** | 99,649 |
| **Edges** | 411,445 |
| **Index Time** | 21s |
| **Dangling Endpoints** | 0 |
| **Contracts Found** | 378 |

---

## ❓ FAQ & Troubleshooting

<details>
<summary><strong>Graph not found / MCP server fails to start</strong></summary>

Run `docker exec lineagelens lineagelens index .` (or `lineagelens index .`) first. The SQLite database is created at `.lineagelens/graph.db` in your project root.
</details>

<details>
<summary><strong>Symbol not found</strong></summary>

Symbols are fully qualified (e.g. `myapp.module.Class.method`). Use `lineagelens query . search <name>` to find the exact symbol ID.
</details>

<details>
<summary><strong>Impact analysis shows 0 affected symbols</strong></summary>

The symbol might be dead code, or callers are in an unindexed language. Run `lineagelens query . callers <symbol>` and inspect `lineagelens coverage .` output.
</details>

<details>
<summary><strong>How do I add a custom framework adapter?</strong></summary>

Adapters are simple YAML files placed under `.lineagelens/adapters/`. See [docs/ADDING-A-LANGUAGE.md](docs/ADDING-A-LANGUAGE.md) for schema details. No Python code modifications required.
</details>

<details>
<summary><strong>Does LineageLens send my source code anywhere?</strong></summary>

No. Source text is never included in uploads. Run `lineagelens index --dry-run` to inspect the exact payload before anything leaves your machine.
</details>

<details>
<summary><strong>How do I verify the graph is reproducible?</strong></summary>

Run `lineagelens verify .`. It rebuilds the graph twice and asserts identical digests. Same commit = same graph.
</details>

---

## 📚 Documentation

- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — Architecture, writing adapters, and core invariants.
- [docs/ADDING-A-LANGUAGE.md](docs/ADDING-A-LANGUAGE.md) — Capability ladder and steps to add a language.
- [docs/TELEMETRY.md](docs/TELEMETRY.md) — Telemetry payload schema and privacy controls.
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — Contribution workflow and guidelines.

---

## 📄 Licence

[AGPL-2.0](LICENSE) (Affero General Public License Version 2)
