# v1.1 — Language ladder, compiler ingestion, and managed graph sync

## Summary

v1.0 shipped the schema-4 rearchitecture (#51): a deterministic multi-language
code graph with control flow, data flow and cross-service contracts in one
SQLite file, reachable from a CLI and an MCP server.

v1.1 closes the distance between that engine and the product surface built on
top of it. The engine is sound — `impact_of`, `dataflow_of`, `contract_map` and
the coverage envelope all work and are measured by the conformance suite. What
is missing is breadth (languages), precision (compiler-grade resolution), and
the path from a local graph to a shared one.

This document is the scope of record. Each workstream below maps to one
sub-issue and one branch; the tracking epic is #54.

## Decisions already taken

Recorded as fixed inputs, not open questions:

- **Language breadth is a capability ladder, not a binary.** A language is not
  "supported" or "unsupported"; it sits at L0/L1/L2 depending on which
  extraction specs exist. Breadth expands at L0; depth is earned per language.
  See §2 below.
- **Tier enforcement is server-side only.** Every feature that runs on the
  user's machine is Apache-2.0 and ungated, including SCIP ingestion and
  resiliency signals. Differentiation lives in the managed service. See §5 below.
- **Auth is the existing device/token flow.** `lineagelens auth login` +
  credentials store. No separate API-key flag on the CLI surface; a
  `LINEAGELENS_TOKEN` env override covers headless CI.
- **Telemetry is the metering substrate, not a checkbox.** It is designed
  once, as the event stream that also backs per-repo and per-branch cost
  attribution in the managed service. See §6 below.
- **No LLM in the extraction path.** Unchanged from #51 §11.

## §1 Current state, measured

Measured on this repository (4,901 nodes / 14,143 edges) and by reading the
code, not the docs.

| Capability | State |
| --- | --- |
| Tree-sitter extraction | 7 logical languages, 8 grammar dialects (`extract/langs.py`) |
| Tier B resolution | Python only, via `JediOracle` |
| Toolchain oracles | javac / tsc / gopls / rust-analyzer / dotnet **detect only** — `ToolchainOracle.resolve()` returns `None` by design (`resolve/oracles.py:206`) |
| SCIP ingestion | Not implemented. `scip` appears as a comment in `store/schema.py:91` and an unused `[scip]` extra |
| Blast radius | Works, with one defect: `report.entry_points` is always empty because `NodeFlags.ENTRY_POINT` is never set — 0 flagged nodes against 13 `EXPOSES` edges (#57) |
| Graph publication | No upload path. The CLI has no network egress outside `auth` |
| Client telemetry | None emitted |
| REST surface | `rest.py` is orphaned. It imports `analyzer`, `config`, `index`, `queries` and `reachability`, all deleted in `59eb115`; the module raises `ModuleNotFoundError` on import and nothing references it |
| Resiliency signals | `list_resiliency_risks` lived in the deleted `queries.py`. No schema-4 equivalent |
| Reachability / dead code | `reachability.py` deleted in `59eb115`. No schema-4 equivalent |

The last three rows share one cause: `59eb115` removed the schema-3 core but
left `rest.py` behind. Because no test imports it, the suite stays green (370
passed) while the entire HTTP surface and the `frontend/` app that calls it are
non-functional.

## §2 Language capability ladder

`conformance/matrix.json` and the `ontology` command already report per-language
capability as `yes` / `part` / `n/a`. This workstream formalises that into three
declared levels and expands breadth at the lowest one.

| Level | Required specs | Delivers | Cost per language |
| --- | --- | --- | --- |
| **L0 — Inventory** | `nodes.scm` | Symbols, search, module/class/function map, `get_symbol` | ~80 lines |
| **L1 — Graph** | `+ refs.scm` | `CALLS`/`IMPORTS`, callers/callees, `impact_of` | +~70 lines |
| **L2 — Flow** | `+ dataflow.scm` | `READS`/`WRITES`/`PARAM_BINDS`/`RETURNS`, `dataflow_of`, contracts | +~80 lines |

Current languages (python, java, typescript, javascript, go, rust, csharp) are
all at L2 and stay there.

**Work:**

1. Add a `level` field to `matrix.json` per language, derived from which specs
   the `SpecRegistry` actually loaded — measured, not declared, consistent with
   #51 §11.
2. Surface the level in `ontology`, in the `coverage` envelope, and in every
   MCP response envelope, so an agent querying a Kotlin file is told it is
   reading an L0 index before it trusts a negative answer.
3. `--require-level=L1` on `index`, generalising the existing
   `--require-tier-b` so CI can demand a floor.
4. Expand to 50+ languages at L0 in waves, each wave adding grammar pins, a
   `nodes.scm`, and a conformance corpus file.

**Constraint that governs this work:** a grammar without a spec is worse than no
support at all. `pyproject.toml` already records why — grammars previously sat
behind an extra with no spec, and Apache Dubbo produced 4,297 "classes" matched
from `"class " in line`. No language enters the matrix without a corpus entry
proving what it extracts.

Grammar pins stay exact, and every wave bumps `graph_meta.grammar_digest`, so
`verify --determinism` keeps working.

## §3 SCIP ingestion as a Tier B oracle

**Builds on #47**, which specifies this well but predates the rearchitecture.
#47 targets `lineagelens/model.CodeGraph`, a `graph.json` artifact and
`lineagelens analyze --scip` — none of which exist after #51. The design is
sound; the integration points change:

| #47 (schema 3) | v1.1 (schema 4) |
| --- | --- |
| `CodeGraph` / `graph.json` | `GraphStore` over SQLite (`store/db.py`) |
| A merger stage after extraction | A `ResolverOracle` in `OracleRegistry` (`resolve/oracles.py`) |
| `lineagelens analyze --scip` | `lineagelens index --scip=<path>` |
| `evidence.label = "scip_compiler"` | `evidence.engine = "scip"`, already reserved in `store/schema.py:91` |

Implementing SCIP as an oracle rather than a merger means it answers the same
two questions every other oracle answers — `resolve(ref)` and `type_of(span)` —
and inherits the existing evidence-tier plumbing, the availability matrix and
the `--require-tier-b` semantics for free.

**Work:**

1. Vendor the generated `scip_pb2` bindings; the `[scip]` extra already
   declares `protobuf`.
2. `ScipOracle`: load `index.scip`, build a
   `(file, line, col) -> symbol` occurrence map, answer `resolve` by matching an
   `UnresolvedRef`'s span against a SCIP `Reference` occurrence and returning
   the `Definition` target.
3. Report `available()` honestly: `detected` when an index exists, with its
   staleness relative to the current commit. A SCIP index older than the
   working tree must degrade to Tier A rather than answer from stale data.
4. Order it ahead of `JediOracle` in `default_oracles` for Python, since a
   compiler-verified fact outranks a static-analysis inference.
5. Conformance cases asserting that a SCIP-resolved edge carries
   `engine="scip"` and that a stale index yields no answer.

Target indexers: `scip-python`, `scip-java`, `scip-typescript`. `scip-go`,
`scip-rust` and `scip-dotnet` follow at no extra engine cost once the oracle
exists — which is the point of choosing SCIP over per-language compiler bridges,
and supersedes the bespoke approach in #26 and #27.

**Consequence for `ToolchainOracle`:** it stays a detector. Rather than wiring
javac and tsserver individually, detection becomes a hint that the user *could*
generate a SCIP index, and the CLI says so.

## §4 Restoring the HTTP surface

`rest.py` is ported onto schema 4 so `frontend/` works again. This is
sequenced first: it is the smallest change, it unblocks the dashboard, and the
broken imports are visible to anyone reading the repository today.

**Work:**

1. Rewrite `rest.py` against `QueryEngine` (`query/api.py`), which already
   exposes every primitive the router needs — `search`, `get_symbol`,
   `callers_of`, `callees_of`, `impact_of`, `trace_flow`, `list_entry_points`.
2. Drop `POST /analyze`; indexing is the CLI's job, and `invalidate`/`load_index`
   no longer exist.
3. Serve the coverage envelope on every response, matching the MCP contract.
4. `/reachability` and `/resiliency` land with §7, not here; until then the
   router does not advertise them.
5. Import `rest` in the test suite so this cannot silently rot again. The root
   cause of this workstream is that nothing did.

## §5 Publishing a graph: `index --upload`

Replaces the removed `analyze --upload`. The graph is built locally and pushed
to ContextServe over the existing token auth.

**Work:**

1. `lineagelens index --upload [--token TOKEN]`, defaulting to the stored
   credential for the active environment and falling back to
   `LINEAGELENS_TOKEN`.
2. Payload: graph nodes and edges, `graph_meta` (build digest, grammar digest,
   schema version), the coverage envelope, per-language capability levels from
   §2, and the derived metrics the dashboard renders.
3. Upload is idempotent on `build_digest` — re-uploading an unchanged graph is a
   no-op, so CI on a retried job does not duplicate.
4. Offline and unauthenticated runs stay first-class: `--upload` is opt-in and
   its failure never fails the index.

**The contract, as it exists today.** `ContextServe/ContextServeWebsite`
implements `POST /api/v1/graphs/upload` in `backend/app/routers/graph_router.py`:

```python
class GraphUploadRequest(BaseModel):
    repo_name: str
    graph_data: dict
```

Authentication is the `X-API-Key` header, checked as a SHA-256 hash against the
`api_keys` table; `generate_api_key()` mints keys in the `ll_live_<6>_<rest>`
form, which is the form the marketing copy shows. So the key in that copy is a
real credential shape — but it is a header, never a CLI flag, and `--key` is
therefore not the right surface. `LINEAGELENS_TOKEN` and the stored credential
remain the path.

Three mismatches to resolve, none of them client-side bugs:

1. **Vocabulary.** The handler reads `graph_data["symbols"]` and
   `graph_data["relations"]`, and looks for `symbol["entry_point"]` and
   `symbol["resiliency"]`. That is schema-3 vocabulary. Schema 4 emits `nodes`
   and `edges`, and entry-point status lives in `NodeFlags` / `EXPOSES`. Either
   the server learns schema 4 or the client ships a lossy projection; the first
   is correct, because a projection would discard exactly the evidence tiers and
   coverage envelope that make the graph worth trusting.
2. **Transport.** The body is one unbounded JSON object with no
   `Content-Encoding` and no digest pre-check. This repository's graph is a
   16 MB SQLite file. Gzip and idempotency on `build_digest` are server-side
   prerequisites for §5's step 3.
3. **A metric with no measurement.** The handler derives `dead_code_ratio` as
   `(total_symbols - entry_points * 3) / total_symbols`, commented in the source
   as a simulation. Dead code is out of scope for v1.1 precisely because the
   schema-3 implementation measured 71% false positives; a formula is not a
   smaller version of that measurement, it is an unfounded number on a
   dashboard. It should be null until a producer exists.

Read-side `/api/v1/graphs/repos` and `/api/v1/orgs/api-keys` also exist, and use
a different auth header (`X-Auth-Token: Bearer`) from the upload route. Worth
unifying server-side.

## §6 Telemetry and cost attribution

One subsystem, two consumers. Opt-in anonymous usage telemetry for the OSS CLI,
and the same event stream as the metering substrate for the managed service
(§7). `/api/v1/telemetry/summary` is already the read side.

**Work:**

1. An event emitter with an explicit consent gate: off until
   `lineagelens telemetry enable` or an authenticated login, honouring
   `DO_NOT_TRACK`. Never on by default, never blocking, never on the hot path.
2. OSS events: command name, duration, node/edge counts, language set,
   capability levels, resolver availability, schema version. No paths, no
   symbol names, no source.
3. Authenticated events add org, repo and branch identity for attribution.
4. Metering at the MCP boundary: per tool call, record tool name, response token
   estimate, graph digest, repo and branch. This is what makes "spend on this
   branch to date" answerable, and the MCP server is the right meter because it
   is where an agent's tokens are actually spent.
5. Document every field. An open-source tool that phones home has to be
   auditable from its own README.

**The contract, as it exists today.** `POST /api/v1/telemetry/tokens` accepts:

```python
class TokenTelemetryCreateRequest(BaseModel):
    raw_tokens: int
    optimized_tokens: int
    query_type: str = "mcp_context_query"
    repo_name: str | None = None
    model_name: str = "gpt-4o"
```

The server derives `tokens_saved`, an efficiency percentage and
`cost_saved_usd` at a hardcoded blended $0.015 per 1k tokens.

Two consequences for step 4:

- **There is no `branch` and no `commit_sha`.** "Spend on this branch to date"
  is the stated differentiator and the current schema cannot express it. The
  client can send the fields once #57 populates them, but the column has to
  exist first. Same for `tool` and `duration_ms` — without `tool`, the question
  "which primitive dominates cost" is unanswerable.
- **`raw_tokens` asks the client for a counterfactual.** It is the token count
  the agent *would* have spent without LineageLens, and the CLI cannot know it.
  Either it becomes a server-side model against a documented baseline, or the
  field is retired in favour of reporting actual spend. Reporting a saving the
  client invented would be the same category of error as the simulated
  `dead_code_ratio`.

## §7 Resiliency signals on schema 4

Rebuilds what `queries.py` provided, on a graph that can now support it.

**Work:**

1. `blocking_in_async`: a blocking call reachable from an async context —
   `Thread.sleep`, `time.sleep`, synchronous I/O — detected over `CALLS` plus
   the async flag on the enclosing callable. Severity from call depth and
   whether the enclosing symbol is an entry point.
2. Language-specific rule packs declared as data, alongside the extraction
   specs, so a rule is reviewable and a language without a pack reports
   `unsupported` rather than silence.
3. `query risks` on the CLI, `list_resiliency_risks` on MCP, `/resiliency` on
   the router.
4. Conformance corpus cases per rule, positive and negative.

**Reachability and dead code are deliberately deferred to a follow-up.** The
prior schema-3 implementation was measured at 71% false positives because the
graph modelled only `CALLS`; a symbol reached through `INSTANTIATES`, a
decorator or a service registry was reported dead. Schema 4 has the edges to fix
this, but the claim needs the dead-code precision benchmark (#49) before it ships
again, not after.

## §8 Tier boundary and the managed service

The engine is Apache-2.0 and ungated. Every capability above runs locally for
free, including SCIP ingestion and resiliency signals. Client-side entitlement
checks are not attempted: in an open repository they are one line to delete, and
attempting them invites a de-gated fork while signalling distrust to the
contributors open-sourcing is meant to attract.

Differentiation is the **managed service** — a hosted, centralised MCP endpoint.
It is defensible because it is not a feature flag; it is compute, storage and a
central ledger the user does not have.

| | Local (OSS, free) | Managed (paid) |
| --- | --- | --- |
| Graph | local SQLite, one repo | hosted, shared, retained, historical |
| Indexing | `lineagelens index` on demand | always-warm, indexed on push |
| Scope | one repository | cross-repo, cross-service contract joins |
| MCP | stdio, per developer | remote endpoint, shared org config |
| Cost | invisible | per-repo, per-branch, per-seat attribution |
| Optimisation | per-developer recomputation | shared result cache, org-wide budget tuning |
| Identity | — | SSO/SAML, seats, policy gates |

Two properties make this the right boundary:

- **Cross-repo is impossible locally.** `contract_map` across 40 services
  requires all 40 graphs. That is the single largest capability gap between the
  columns and no fork closes it.
- **The MCP server is the natural meter.** Query cost grows with the number of
  calls, not the size of any one answer, so the MCP boundary is exactly where
  tokens are spent and therefore where they can be counted and reduced.

This also fixes a boundary error in current positioning: blast radius and
resiliency signals are currently presented as paid, but they run locally in the
OSS CLI. They move to free, and their paid counterpart becomes the org-wide,
every-PR, alerting version. The free tier becomes the acquisition path rather
than a withheld one.

**Naming:** "CodeLens" collides with the established VS Code editor feature and
will lose search traffic and invite confusion. The existing site positioning —
*"Enterprise Context Management Suite & AI Token Gateway"* — already names this
product correctly. The managed MCP endpoint **is** the token gateway; naming it
to match makes the gateway concrete instead of aspirational.

## §9 Sequencing and issues

| Order | Issue | Workstream | Branch |
| --- | --- | --- | --- |
| 1 | #55 | §4 HTTP surface | `fix/rest-schema4` |
| 2 | #56 | §2 Ladder machinery | `feature/language-ladder` |
| 3 | #58 | §5 Upload | `feature/cli-index-upload` |
| 4 | #59 | §6 Telemetry | `feature/telemetry` |
| 5 | #60 | §7 Resiliency | `feature/resiliency-signals` |
| 6 | #47 | §3 SCIP oracle | `feature/scip-oracle` |
| 7 | #61 | §2 Grammar waves | `feature/grammar-wave-N` |

## §10 Out of scope

- Reachability and dead-code detection (follow-up, gated on #49).
- Wiring javac/tsserver/gopls/rust-analyzer individually — superseded by §3.
- The managed service backend itself; §5–§6 build only the client side.
- Any LLM in the extraction path (#51 §11).

## §11 Repository boundary: what lands where

Two repositories, and the line between them is load-bearing: everything in this
one is Apache-2.0 and ungated, so anything that must not be removable by a fork
cannot live here.

### This repository — `ContextServe/LineageLens`

All eight v1.1 issues. Every one of them is client-side: extraction, query,
CLI, MCP, the local dashboard's HTTP surface, and the *emitting* half of upload
and telemetry. No issue in this repository reads a plan, checks an entitlement,
or branches on a tier.

### `ContextServe/ContextServeWebsite` — needs its own issues

These block or shadow v1.1 work but are not LineageLens changes:

| Server work | Blocks |
| --- | --- |
| Ingest accepts schema 4 (`nodes`/`edges`/`flags`/envelope) instead of schema-3 `symbols`/`relations` | #58 |
| Gzip body and `build_digest` pre-check on `POST /graphs/upload` | #58 step 3 |
| Null out the simulated `dead_code_ratio` until a producer exists | #58, epic out-of-scope |
| `branch`, `commit_sha`, `tool`, `duration_ms` columns on `token_telemetry` | #59 step 4 |
| Decide `raw_tokens`: server-side baseline, or retire it | #59 |
| Unify auth headers (`X-API-Key` on write, `X-Auth-Token: Bearer` on read) | both |
| Enforce `plan_tier` — set by Stripe, read for display, checked nowhere | §8 |
| The managed MCP endpoint | §8 |
| Copy: `analyze --upload --key=` → `index --upload`; language claim → the ladder; Tier 2 scope | — |

The last row is a documentation change, not an engineering one, but it is the
one a reader encounters first.

### How tier enforcement actually works

Three server-side chokepoints, and nothing else:

1. **Ingest.** `POST /graphs/upload` already resolves an `Organization` from the
   API key, so it already has `org.plan_tier` in hand. Repo count, graph size
   and retention are enforced here, as a FastAPI dependency
   (`enforce_repo_quota(org)`) rather than conditionals inside the handler, so
   the check cannot be forgotten on a new route.
2. **The managed MCP endpoint.** Seats, rate, and cross-repo joins.
3. **Read APIs.** History window on `/telemetry/summary` and `/graphs/repos`.

A rejection is an HTTP status the CLI renders, never a decision the CLI makes:
`402` for a plan limit, `403` for a seat or policy gate, each with a message and
a remedy. The CLI's only job is to print it and exit 0 if the local index
succeeded.

**Why a fork cannot route around this.** The paid capability is not a flag; it
is a corpus. `contract_map` across 40 services needs all 40 graphs, always
warm — that is storage, scheduled indexing and a central ledger, none of which
a de-gated client can synthesise. Cost attribution has the same shape: a
per-developer CLI cannot total an organisation's spend because it only ever
sees its own. Deleting a check from a fork yields an unlimited client talking to
a server that still counts.

The corollary is that the free tier has to be genuinely good, and v1.1 makes it
so: blast radius, resiliency signals and SCIP ingestion all run locally at no
cost. That is the acquisition path, and it only works if nothing in this
repository is crippled on purpose.
