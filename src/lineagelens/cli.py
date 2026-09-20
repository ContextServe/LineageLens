"""Command line interface.

Six commands, replacing the schema-3 surface. Two are gone deliberately:

* ``analyze`` -> ``index``. The old command dispatched on the *repository*
  language with an early return, so a polyglot repo produced one language and
  silently discarded the rest. Running it on this repository emitted 39
  TypeScript symbols and zero Python, overwriting a good graph in place.
* ``--engine`` is gone entirely. There is no user-selectable engine: extraction
  is per-file and additive. ``--dataflow`` went the same way: data flow is
  always computed, because deferring it dropped 69% of the data-flow edges to
  save 3.2s, and its premise -- that a query touches a small slice -- does not
  hold when resolution is global.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .core import SCHEMA_VERSION, Intent
from .indexer import Indexer
from .ontology import capability_matrix
from .query import QueryEngine
from .store import DB_FILENAME, GraphNotFound, SchemaMismatch

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lineagelens",
        description=(
            "Deterministic code graph: control flow, data flow and "
            "cross-service contracts, for humans and coding agents."
        ),
    )
    parser.add_argument("--version", action="version",
                        version=f"lineagelens schema {SCHEMA_VERSION}")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log extraction and resolution progress")
    commands = parser.add_subparsers(dest="command", required=True)

    _add_index(commands)
    _add_query(commands)
    _add_coverage(commands)
    _add_verify(commands)
    _add_ontology(commands)
    _add_mcp(commands)
    _add_auth(commands)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    auth_cmds = {"auth", "help", "version"}
    is_auth_cmd = hasattr(args, "auth_command") or getattr(args, "command", "") in auth_cmds
    
    # Require authentication for core commands
    if not is_auth_cmd:
        import os
        if os.environ.get("LINEAGELENS_TOKEN"):
            return int(args.handler(args) or 0)
            
        from .credentials import CredentialsStore
        store = CredentialsStore()
        active_env = store.active_env
        creds = store.get(active_env)
        
        # If no credentials or expired (handled by get()), force login
        if not creds or not store.is_token_valid(active_env):
            print(f"  \u2717 You are not logged in to {active_env}.")
            print("  Automatically starting login flow...\n")
            from .cli import _run_auth
            # Mock args for login
            class LoginArgs:
                auth_command = "login"
                env = active_env
                no_browser = False
                timeout = 300
                reauth = False
            _run_auth(LoginArgs())
            print() # Blank line after login

    return int(args.handler(args) or 0)


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

def _add_index(commands: Any) -> None:
    cmd = commands.add_parser(
        "index",
        help="build the graph for a project (all languages, one graph)",
    )
    cmd.add_argument("path", nargs="?", default=".", type=Path)
    cmd.add_argument(
        "--require-tier-b", nargs="?", const="*", default="",
        help=(
            "SKIP languages that have no native type resolver, instead of "
            "indexing them at Tier A. Pass a comma-separated list, or the flag "
            "alone for every language. Use in CI when a partial graph must be "
            "an error. By default every language is indexed and the resolution "
            "tier is reported per language in the coverage envelope"
        ),
    )
    cmd.add_argument("--force", action="store_true",
                     help="rebuild even if the index looks current")
    cmd.add_argument("--json", action="store_true", help="emit the report as JSON")
    cmd.set_defaults(handler=_run_index)


def _run_index(args: Any) -> int:
    indexer = Indexer(
        args.path,
        require_tier_b=_tier_b_set(args.require_tier_b),
    )
    
    import threading
    
    result = []
    exc = []
    
    def worker():
        try:
            store, report = indexer.run()
            store.close()
            result.append(report)
        except Exception as e:
            exc.append(e)

    if not args.json:
        print("  Indexing ", end="", flush=True)

    t = threading.Thread(target=worker)
    t.start()
    
    while t.is_alive():
        if not args.json:
            print(".", end="", flush=True)
        t.join(0.5)
        
    if not args.json:
        print()

    if exc:
        raise exc[0]
        
    report = result[0]
    
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        _print_index_report(report)
    return 0


def _tier_b_set(raw: str) -> frozenset[str]:
    """Parse ``--require-tier-b``. Bare flag means every language."""
    if not raw:
        return frozenset()
    if raw == "*":
        from .extract import supported_languages
        return supported_languages()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _print_index_report(report: Any) -> None:
    data = report.as_dict()
    files = data["files"]
    graph = data["graph"]

    print(f"indexed {data['project_root']}")
    print(
        f"  files     {files['parsed']:,} parsed"
        + (f", {files['skipped']:,} skipped" if files["skipped"] else "")
        + (f", {files['failed']:,} failed" if files["failed"] else "")
    )
    langs = ", ".join(f"{k} {v:,}" for k, v in data["languages"].items())
    print(f"  languages {langs or 'none'}")
    print(f"  services  {data['services']:,}")
    print(f"  graph     {graph['nodes']:,} nodes, {graph['edges']:,} edges")
    print(
        f"  contracts {data['contracts']:,}"
        f"   unresolved {graph['unresolved_refs']:,}"
        f"   boundaries {graph['boundaries']:,}"
    )
    print(f"  digest    {data['build_digest'][:16]}  ({data['duration_seconds']}s)")

    if data.get("tier_a_only"):
        print(
            f"  tier A only: {', '.join(data['tier_a_only'])}"
            f"  (no type resolver; more refs ambiguous, none guessed)"
        )

    if data["skipped_reasons"]:
        print("  skipped:")
        for reason, count in data["skipped_reasons"].items():
            print(f"    {reason:20s} {count:,}")

    # A framework nobody wrote an adapter for is a coverage gap, so it is
    # surfaced as a work list rather than left silent (§8.2).
    unclaimed = data.get("unclaimed_frameworks") or []
    if unclaimed:
        print("\n  frameworks present but unmodelled (no contract adapter):")
        for entry in unclaimed[:5]:
            print(
                f"    {entry['name']:24s} {entry['uses']:>4} uses"
                f"   e.g. {entry['example']}  {entry.get('sample_key', '')}"
            )
        print("    add an adapter under .lineagelens/adapters/ to link these")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------

def _add_query(commands: Any) -> None:
    cmd = commands.add_parser("query", help="ask the graph a question")
    cmd.add_argument("path", nargs="?", default=".", type=Path)

    # The shared flags are attached to every primitive as a parent parser, not
    # only to `query`. argparse consumes options positionally, so declaring
    # them once on the parent would force `query --json . impact f.py:4` --
    # nobody writes that, and `query . impact f.py:4 --json` would fail with an
    # unrecognised-argument error that gives no hint why.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--json", action="store_true", help="emit raw JSON")
    shared.add_argument("--intent", choices=[i.value for i in Intent],
                        help="precise (debugging) or plan (surveying)")
    shared.add_argument("--limit", type=int)
    shared.add_argument("--max-depth", type=int)
    shared.add_argument("--kinds", help="comma-separated edge kinds to follow")

    cmd.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    cmd.add_argument("--intent", choices=[i.value for i in Intent],
                     help=argparse.SUPPRESS)
    cmd.add_argument("--limit", type=int, help=argparse.SUPPRESS)
    cmd.add_argument("--max-depth", type=int, help=argparse.SUPPRESS)
    cmd.add_argument("--kinds", help=argparse.SUPPRESS)

    sub = cmd.add_subparsers(dest="primitive", required=True)

    def add(name: str, help_text: str, *arguments: str) -> Any:
        parser = sub.add_parser(name, help=help_text, parents=[shared])
        for argument in arguments:
            parser.add_argument(argument)
        return parser

    add("paths", "the call chains between two symbols", "from_symbol", "to_symbol")
    add("trace", "a call tree rooted at one symbol", "entry")
    add("callers", "who reaches this symbol, with chains", "symbol")
    add("callees", "what this symbol reaches, with chains", "symbol")
    add("impact", "what changing a symbol or file:line affects", "target")
    add("dataflow", "where a value comes from and goes", "symbol")
    add("similar", "how this repo already wires a flow like this", "exemplar")
    add("explain", "why two symbols are believed related", "from_symbol", "to_symbol")
    add("symbol", "one symbol with signature, docs and source", "symbol")
    add("search", "ranked search over the graph", "text")
    add("entrypoints", "symbols exposing a contract")
    add("contracts", "who exposes and consumes what")

    stack = sub.add_parser("stacktrace", parents=[shared],
                           help="map a stack trace onto the graph")
    stack.add_argument("file", nargs="?", help="file containing the trace; "
                                               "reads stdin when omitted")

    diff = sub.add_parser("diff", parents=[shared],
                          help="impact of a unified diff")
    diff.add_argument("file", nargs="?", help="diff file; reads stdin when omitted")

    cmd.set_defaults(handler=_run_query)


def _run_query(args: Any) -> int:
    try:
        engine = QueryEngine.open(args.path)
    except (GraphNotFound, SchemaMismatch) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    kinds = (
        [k.strip() for k in args.kinds.split(",") if k.strip()]
        if args.kinds else None
    )
    shared = {"intent": args.intent, "limit": args.limit}
    walk = {**shared, "kinds": kinds, "max_depth": args.max_depth}
    name = args.primitive

    if name == "paths":
        result = engine.find_paths(args.from_symbol, args.to_symbol, **walk)
    elif name == "trace":
        result = engine.trace_flow(args.entry, **walk)
    elif name == "callers":
        result = engine.callers_of(args.symbol, **walk)
    elif name == "callees":
        result = engine.callees_of(args.symbol, **walk)
    elif name == "impact":
        result = engine.impact_of(
            args.target, intent=args.intent, limit=args.limit,
            max_depth=args.max_depth,
        )
    elif name == "dataflow":
        result = engine.dataflow_of(
            args.symbol, intent=args.intent, limit=args.limit,
            max_depth=args.max_depth,
        )
    elif name == "similar":
        result = engine.similar_flows(args.exemplar, limit=args.limit)
    elif name == "explain":
        result = engine.explain(args.from_symbol, args.to_symbol)
    elif name == "symbol":
        result = engine.get_node(args.symbol, intent=args.intent)
    elif name == "search":
        result = engine.search(args.text, kinds=kinds, **shared)
    elif name == "entrypoints":
        result = engine.entry_points(limit=args.limit)
    elif name == "contracts":
        result = engine.contract_map(limit=args.limit)
    elif name == "stacktrace":
        result = engine.map_stacktrace(_read_input(args.file), limit=args.limit)
    elif name == "diff":
        result = engine.impact_of_diff(
            _read_input(args.file), intent=args.intent, limit=args.limit
        )
    else:  # pragma: no cover - argparse rejects anything else
        print(f"unknown primitive: {name}", file=sys.stderr)
        return 2

    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_result(payload)
    engine.store.close()
    return 1 if payload.get("error") else 0


def _read_input(path: str | None) -> str:
    if path:
        return Path(path).read_text("utf-8", errors="replace")
    return sys.stdin.read()


def _print_result(payload: dict[str, Any]) -> None:
    """Human-readable rendering. JSON stays the machine contract."""
    if payload.get("error"):
        print(f"error: {payload['error']}", file=sys.stderr)
        if payload.get("remedy"):
            print(f"  {payload['remedy']}", file=sys.stderr)
        return

    header = payload["kind"]
    if payload["total_available"]:
        header += f"  {payload['returned']} of {payload['total_available']}"
        if payload["truncated"]:
            header += " (truncated -- raise --limit)"
    print(header)

    for item in payload["results"]:
        print()
        if isinstance(item, dict) and "chain" in item:
            print(f"  [{item['length']} hop{'s' if item['length'] != 1 else ''}] "
                  f"{item['chain']}")
            for hop in item.get("hops", []):
                via = f" via {hop['via']}" if hop.get("via") else ""
                print(f"      {hop['depth']}. {hop['node']}  ({hop['at']}){via}")
        else:
            print(_indent(json.dumps(item, indent=2, default=str), "  "))

    coverage = payload.get("coverage", {})
    if coverage:
        print()
        state = "complete" if coverage.get("complete") else "qualified"
        print(f"  coverage: {state}  (intent={coverage.get('intent')})")
        for entry in coverage.get("boundary_detail", [])[:5]:
            print(f"    boundary {entry['kind']}: {entry['detail']}")
        if coverage.get("degraded"):
            print(f"    degraded (Tier A only): {', '.join(coverage['degraded'])}")


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ---------------------------------------------------------------------------
# coverage / verify / ontology / mcp
# ---------------------------------------------------------------------------

def _add_coverage(commands: Any) -> None:
    cmd = commands.add_parser(
        "coverage", help="what the index does and does not cover"
    )
    cmd.add_argument("path", nargs="?", default=".", type=Path)
    cmd.add_argument("--scope", help="restrict the per-file detail to a path prefix")
    cmd.add_argument("--json", action="store_true")
    cmd.set_defaults(handler=_run_coverage)


def _run_coverage(args: Any) -> int:
    try:
        engine = QueryEngine.open(args.path)
    except (GraphNotFound, SchemaMismatch) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    payload = engine.coverage_report(scope=args.scope).as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        counts = payload["counts"]
        print(f"nodes {counts['nodes']:,}   edges {counts['edges']:,}   "
              f"unresolved {counts['unresolved_refs']:,}   "
              f"boundaries {counts['boundaries']:,}")
        print("\nnode kinds:")
        for kind, count in payload["node_kinds"].items():
            print(f"  {kind:14s} {count:,}")
        print("\nedge kinds:")
        for kind, count in payload["edge_kinds"].items():
            print(f"  {kind:14s} {count:,}")
        coverage = payload["coverage"]
        if coverage.get("refs"):
            refs = coverage["refs"]
            print(f"\nreferences: {refs['total']:,} observed  "
                  f"{refs['exact']:.0%} exact  {refs['inferred']:.0%} inferred  "
                  f"{refs['unresolved']:.0%} unresolved")
            print("  unresolved is mostly genuinely external (stdlib, "
                  "third-party) plus ambiguities recorded with candidates")
        if coverage.get("files"):
            print("files: " + "  ".join(
                f"{k} {v:,}" for k, v in coverage["files"].items()
            ))
        if coverage.get("degraded"):
            print(f"degraded (Tier A only): {', '.join(coverage['degraded'])}")
    engine.store.close()
    return 0


def _add_verify(commands: Any) -> None:
    cmd = commands.add_parser(
        "verify",
        help="rebuild twice and confirm the graph is byte-identical (§11)",
    )
    cmd.add_argument("path", nargs="?", default=".", type=Path)
    cmd.add_argument("--require-tier-b", nargs="?", const="*", default="")
    cmd.set_defaults(handler=_run_verify)


def _run_verify(args: Any) -> int:
    """Determinism check (§11, §16.24).

    Two independent builds of the same tree must produce the same digest. A
    grammar upgrade, an unsorted iteration, or a wall-clock value leaking into
    a stored row would all break this -- which is the point of checking rather
    than intending.
    """
    import tempfile

    allow = _tier_b_set(args.require_tier_b)
    digests: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for run in (1, 2):
            store, report = Indexer(
                args.path, require_tier_b=allow,
            ).run(db_path=Path(tmp) / f"run{run}" / DB_FILENAME)
            digests.append(report.build_digest)
            store.close()
            print(f"  build {run}: {report.build_digest}")

    if digests[0] != digests[1]:
        print("\nFAIL: two builds of the same tree differ.", file=sys.stderr)
        print("Something order-dependent or time-dependent reached the store.",
              file=sys.stderr)
        return 1
    print("\nOK: deterministic.")

    return 0


def _add_ontology(commands: Any) -> None:
    cmd = commands.add_parser(
        "ontology", help="what this build can express, measured not declared"
    )
    cmd.add_argument("path", nargs="?", default=".", type=Path)
    cmd.add_argument("--json", action="store_true")
    cmd.set_defaults(handler=_run_ontology)


def _run_ontology(args: Any) -> int:
    matrix = capability_matrix(Path(args.path))
    if args.json:
        print(json.dumps(matrix, indent=2, default=str))
        return 0

    print(f"schema {matrix['schema_version']}   "
          f"{len(matrix['node_kinds'])} node kinds   "
          f"{len(matrix['relation_kinds'])} relation kinds")
    if not matrix["generated"]:
        print("\nconformance matrix not generated; per-language capability is "
              "reported as 'untested'")

    columns = ("nodes", "calls", "inherits", "implements", "dataflow", "contracts")
    symbols = {True: "yes", "partial": "part", "untested": "?", False: "-",
               "n/a": "n/a"}
    header = "  ".join(f"{name[:9]:>9s}" for name in columns)
    print(f"\n{'language':11s} {'type resolver':26s} {header}")
    for lang, entry in matrix["languages"].items():
        caps = entry.get("capabilities", {})
        marks = "  ".join(
            f"{symbols.get(caps.get(name), '-'):>9s}" for name in columns
        )
        print(f"{lang:11s} {entry['tier_b'][:26]:26s} {marks}")
    print("\n  'n/a' means the language has no such construct; 'part' is a "
          "documented partial (see `--json` for the reason).")

    project = matrix.get("project", {})
    if project.get("indexed"):
        print(f"\nthis index: {project['counts']['nodes']:,} nodes, "
              f"languages {', '.join(project['languages_present'])}")
        if project.get("languages_skipped"):
            print("  skipped: " + ", ".join(
                f"{k} ({v})" for k, v in project["languages_skipped"].items()
            ))
    return 0


def _add_mcp(commands: Any) -> None:
    cmd = commands.add_parser("mcp", help="run the MCP server over stdio")
    cmd.add_argument("path", nargs="?", default=".", type=Path)
    cmd.set_defaults(handler=_run_mcp)


def _run_mcp(args: Any) -> int:
    from .mcp.server import create_server

    create_server(Path(args.path)).run()
    return 0


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def _add_auth(commands: Any) -> None:
    _envs = ["prod", "stage", "dev", "local"]
    auth_cmd = commands.add_parser("auth", help="Authenticate with ContextServe.ai")
    auth_sub = auth_cmd.add_subparsers(dest="auth_command", required=True)

    login_p = auth_sub.add_parser("login", help="Log in via browser + one-time code")
    login_p.add_argument("--env", "-e", default=None, choices=_envs,
                         help="Target environment (defaults to active environment)")
    login_p.add_argument("--no-browser", action="store_true",
                         help="Print URL instead of opening browser (useful over SSH)")
    login_p.add_argument("--reauth", action="store_true",
                         help="Re-authenticate even if a valid token already exists")
    login_p.add_argument("--timeout", type=int, default=300,
                         help="Seconds to wait for browser login (default: 300)")

    logout_p = auth_sub.add_parser("logout", help="Revoke and remove stored credentials")
    logout_p.add_argument("--env", "-e", default=None, choices=_envs)
    logout_p.add_argument("--all", action="store_true",
                          help="Log out of all environments")

    auth_sub.add_parser("status", help="Show authentication status for all environments")

    token_p = auth_sub.add_parser("token", help="Print raw bearer token to stdout")
    token_p.add_argument("--env", "-e", default=None, choices=_envs)

    switch_p = auth_sub.add_parser("switch-env", help="Change active environment")
    switch_p.add_argument("env", choices=_envs)

    auth_cmd.set_defaults(handler=_run_auth)


def _run_auth(args: Any) -> int:
    """Handle all ``lineagelens auth`` subcommands."""
    from .auth_flow import (
        ENVIRONMENTS,
        LoginError,
        LoginRejected,
        LoginTimeout,
        run_login,
    )
    from .credentials import CredentialsStore

    store = CredentialsStore()
    sub = args.auth_command

    # ── login ─────────────────────────────────────────────────────────────────
    if sub == "login":
        env = args.env or store.active_env

        if not args.reauth and store.is_token_valid(env):
            creds = store.get(env)
            print()
            print(f"  \u2713 Already logged in as {creds['email']} ({env})")
            print(f"  Token valid until: {creds['expires_at']}")
            print()
            print("  To re-authenticate:          lineagelens auth login --reauth")
            print("  To login to another env:     lineagelens auth login --env <env>")
            return 0

        try:
            token_resp = run_login(
                env=env,
                no_browser=args.no_browser,
                timeout=args.timeout,
            )
        except LoginTimeout as exc:
            raise SystemExit(f"\n  \u2717 {exc}") from exc
        except LoginRejected as exc:
            raise SystemExit(f"\n  \u2717 {exc}") from exc
        except LoginError as exc:
            raise SystemExit(f"\n  \u2717 {exc}") from exc

        base_url = ENVIRONMENTS[env]
        store.save(env, token_resp, base_url)

        email = token_resp.get("email", "")
        if not email:
            # Fetch from /me if exchange didn't include it
            try:
                import httpx
                with httpx.Client(base_url=base_url, timeout=10) as client:
                    me = client.get(
                        "/api/v1/auth/me",
                        headers={"X-Auth-Token": f"Bearer {token_resp['access_token']}"},
                    )
                    if me.is_success:
                        email = me.json().get("email", "")
                        store.save(env, token_resp, base_url, email=email)
                    else:
                        import sys
                        print(f"  \u26a0\ufe0f Could not fetch user profile: HTTP {me.status_code} - {me.text}", file=sys.stderr)
            except Exception as e:
                import sys
                print(f"  \u26a0\ufe0f Could not fetch user profile (Network Error): {e}", file=sys.stderr)

        print()
        print(f"  \u2713 Logged in as {email or '(unknown)'}")
        print(f"  \u2713 Environment : {env}")
        print(f"  \u2713 Organization: {token_resp.get('organization_name', '')}"
              f"  ({token_resp.get('plan_tier', '')})")
        creds = store.get(env)
        if creds:
            print(f"  \u2713 Token valid until: {creds['expires_at']}")
        print()
        print("  Next steps:")
        print("    lineagelens auth switch-env prod   # switch environment")
        print("    lineagelens auth status            # view all sessions")

    # ── logout ────────────────────────────────────────────────────────────────
    elif sub == "logout":
        if args.all:
            store.remove_all()
            print("  \u2713 Logged out of all environments.")
        else:
            env = args.env or store.active_env
            removed = store.remove(env)
            if removed:
                print(f"  \u2713 Logged out of '{env}'.")
            else:
                print(f"  \u2022 No credentials found for '{env}'.")

    # ── status ────────────────────────────────────────────────────────────────
    elif sub == "status":
        all_creds = store.all_envs()
        active = store.active_env
        all_envs = ["prod", "stage", "dev", "local"]

        print()
        print(f"  {'Environment':<10}  {'User':<30}  {'Status':<8}  Expires")
        _dash10 = "\u2500" * 10
        _dash30 = "\u2500" * 30
        _dash8  = "\u2500" * 8
        _dash20 = "\u2500" * 20
        print(f"  {_dash10}  {_dash30}  {_dash8}  {_dash20}")
        for env in all_envs:
            creds = all_creds.get(env)
            marker = "\u2713" if creds else "\u2717"
            user_col = creds["email"] if creds else "(not authenticated)"
            status_col = (
                "active" if (creds and store.is_token_valid(env))
                else ("expired" if creds else "")
            )
            expires_col = creds.get("expires_at", "") if creds else ""
            active_marker = " \u25c0" if env == active else ""
            print(f"  {env:<10}  {marker} {user_col:<28}  {status_col:<8}  "
                  f"{expires_col}{active_marker}")

        print()
        print(f"  Active environment: {active}")
        print()

    # ── token ─────────────────────────────────────────────────────────────────
    elif sub == "token":
        env = args.env or store.active_env
        creds = store.get(env)
        if not creds or not creds.get("access_token"):
            raise SystemExit(
                f"\u2717 Not authenticated for '{env}'. "
                f"Run: lineagelens auth login --env {env}"
            )
        # Write only the raw token — no trailing newline noise for scripts
        print(creds["access_token"], end="")
        sys.stdout.flush()

    # ── switch-env ────────────────────────────────────────────────────────────
    elif sub == "switch-env":
        env = args.env
        store.set_active_env(env)
        creds = store.get(env)
        if creds:
            print(f"  \u2713 Active environment set to '{env}' ({creds['email']})")
        else:
            print(f"  \u2713 Active environment set to '{env}'")
            print(f"  \u26a0  Not yet authenticated. "
                  f"Run: lineagelens auth login --env {env}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
