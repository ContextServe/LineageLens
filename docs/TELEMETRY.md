# Telemetry

**Off until you turn it on.** No events are sent before
`lineagelens telemetry enable`.

```
lineagelens telemetry enable      opt in
lineagelens telemetry disable     opt out
lineagelens telemetry status      the exact payload, and where it goes
```

`telemetry status` prints the **real event**, not a description of one. For a
tool that ships as source, nobody should have to read `telemetry.py` to find
out what leaves their machine.

## Overrides you cannot configure away

| Variable | Effect |
| --- | --- |
| `DO_NOT_TRACK` | suppresses everything |
| `CI` | suppresses everything |

Both win over the preference file, permanently. A build server cannot consent
on behalf of the person who wrote the config, and a config file should not be
able to opt a CI fleet in.

`telemetry enable` says so plainly if one of these is set, rather than
reporting success and then sending nothing.

**Logging in is not consent for anonymous telemetry.** Authenticating to a
hosted service is consent for *that service*. The two flags are separate and
`is_enabled()` does not consult your credentials.

## Where the preference lives

`~/.config/lineagelens/telemetry.json`, beside your credentials — **not** in the
project. A telemetry choice is per user, not per repository; committing one
would opt a whole team in by accident. Written `0600`.

It holds two things: whether you opted in, and a random UUID.

**The install id is random, never derived.** Not from hostname, username, MAC
or project path. A derived identifier is a fingerprint wearing an anonymity
label, and the only honest way to have a stable anonymous id is to generate one.

## What an anonymous event contains

```json
{
  "event": "command",
  "command": "index",
  "duration_ms": 1234,
  "exit_code": 0,
  "install_id": "9820fe5c-db92-4c63-8f25-2f46f31b0408",
  "schema_version": 5,
  "python": "3.14.6",
  "os": "darwin",
  "arch": "arm64",
  "nodes": 5081,
  "edges": 14719,
  "unresolved": 7163,
  "boundaries": 1098,
  "files_parsed": 67,
  "files_skipped": 0,
  "languages": {"python": 55, "typescript": 7, "java": 1},
  "levels": {"python": "L2", "typescript": "L2"},
  "grammar_digest": "…",
  "spec_digest": "…",
  "ontology_digest": "…"
}
```

Counts, versions and digests. Nothing else.

## What is never sent

```
path  file  file_path  project_root  repo  repo_name  remote  branch
commit_sha  symbol  qualified_name  name  signature  docstring  source
context_line  candidates
```

A repository name is identifying. A language count is not. That is the whole
line.

This is enforced, not documented: `assert_anonymous` walks every key in the
event and raises if one of the above appears, and a test asserts it over a real
payload. So a field added later by someone who has not read this file still
fails the build.

It walks **keys**, not serialised text — a language name or a docstring may
legitimately contain the word "path", and a check that cries wolf is a check
that gets switched off.

## MCP metering

Separate from anonymous telemetry, and only for an authenticated session.

Every MCP tool call records:

| Field | Why |
| --- | --- |
| `tool` | which primitive was called — the actionable cost lever |
| `duration_ms` | latency per tool, to find the slow ones |
| `response_bytes` | what actually crossed the wire |
| `estimated_tokens` | `response_bytes / 4`. **An estimate, named as one** |
| `intent` | `plan` vs `precise`; `precise` adds verbatim source and data flow |
| `result_count`, `truncated` | whether a budget was hit |

```
$ # after a session
{
  "calls": 3,
  "estimated_tokens": 458,
  "by_tool": {
    "callers_of":      {"calls": 1, "estimated_tokens": 82},
    "coverage_report": {"calls": 1, "estimated_tokens": 227},
    "search":          {"calls": 1, "estimated_tokens": 149}
  }
}
```

**`estimated_tokens` is an estimate and says so in its name.** This process
does not tokenise and should not pretend to. Billing on an estimate would be
indefensible; reporting on one is fine when it is labelled.

Metering lives in the `@guarded` decorator every tool already goes through, so
it is one change rather than twenty-four — and a new tool is metered by default
rather than by someone remembering.

It is a counter, never I/O. Nothing is sent until the process exits, because a
meter that costs a network round trip per call becomes the cost it is measuring.

The MCP boundary is the right place to measure, and not incidentally: query cost
grows with the **number of calls**, not the size of any one answer. That is why
the tool descriptions steer agents toward `explore()` over a fan-out of
single-symbol calls — and why the boundary where tokens are spent is the
boundary where they can be counted and reduced.

## Failure is invisible

Bounded 3-second timeout, no foreground retries, every exception swallowed to
debug, buffer capped at 500 events with the drop count reported rather than
hidden.

A telemetry endpoint being down, slow or wrong must not be able to change an
exit code. A test asserts that an endpoint failure, a timeout and a malformed
response all leave `index` unaffected.

## Retention

Anonymous events: aggregated counts retained indefinitely, raw events 90 days.
Authenticated metering follows your organisation's plan. Both are server-side
concerns and live in the hosted service's own policy; this document covers what
the client sends.

## Local SQLite Metrics & Token Savings Reporter (`lineagelens report`)

When running offline or unauthenticated, invocation metrics and token reduction telemetry are persisted locally to `.lineagelens/metrics.sqlite` using SQLite WAL mode with zero latency overhead.

```bash
lineagelens report                    # Visual summary table and cost savings
lineagelens report --by-tool          # Detailed per-tool breakdown
lineagelens report --period 7d        # Filter by time window (today, 7d, 30d, all)
lineagelens report --model claude-3-5-sonnet  # Pricing model baseline
lineagelens report --sync             # Report unsynced metrics to ContextServe.ai
lineagelens report --json             # Machine-readable JSON output
lineagelens report --reset -y         # Reset local metrics history
```

### Counterfactual Baseline vs. Optimized Tokens
- **Raw Tokens (Counterfactual)**: Measures the tokens an agent would have spent loading full source files containing the touched nodes into its context window.
- **Optimized Tokens**: The surgical LineageLens JSON response size converted at 4 bytes per token.
- **Tokens Saved**: `raw_tokens - optimized_tokens`.
- **Database Safety**: Stored in `.lineagelens/metrics.sqlite`, ensuring metrics outlive code graph rebuilds (`lineagelens index . --force`).

