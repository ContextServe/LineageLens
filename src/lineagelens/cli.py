"""Command line interface.

Six commands, replacing the schema-3 surface. Two are gone deliberately:

* ``analyze`` -> ``index``. The old command dispatched on the *repository*
  language with an early return, so a polyglot repo produced one language and
  silently discarded the rest. Running it on this repository emitted 39
  TypeScript symbols and zero Python, overwriting a good graph in place.
* ``--engine`` is gone entirely. There is no user-selectable engine: extraction
  is per-file and additive, and the choice a user actually has is a fidelity
  budget (``--dataflow``), not an analyser.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .core import SCHEMA_VERSION, DataflowMode, Intent
from .indexer import Indexer
from .ontology import capability_matrix
from .query import QueryEngine
from .store import DB_FILENAME, GraphNotFound, GraphStore, SchemaMismatch

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

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
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
        "--dataflow", choices=[m.value for m in DataflowMode],
        default=DataflowMode.LAZY.value,
        help=(
            "when to compute data-flow edges. lazy (default) defers them per "
            "function body; eager computes everything up front; incremental "
            "recomputes only changed files"
        ),
    )
    cmd.add_argument(
        "--allow-tier-a-only", default="",
        help=(
            "comma-separated languages to index without a type resolver. "
            "Without this, a language with no available resolver is SKIPPED "
            "rather than approximated by name matching -- see `lineagelens "
            "ontology` for what is available here. Edges produced this way are "
            "marked heuristic and reported in every coverage envelope"
        ),
    )
    cmd.add_argument("--force", action="store_true",
                     help="rebuild even if the index looks current")
    cmd.add_argument("--json", action="store_true", help="emit the report as JSON")
    cmd.set_defaults(handler=_run_index)


def _run_index(args: Any) -> int:
    allow = frozenset(
        part.strip() for part in args.allow_tier_a_only.split(",") if part.strip()
    )
    indexer = Indexer(
        args.path,
        dataflow=DataflowMode(args.dataflow),
        allow_tier_a_only=allow,
    )
    store, report = indexer.run()
    try:
        if args.json:
            print(json.dumps(report.as_dict(), indent=2))
        else:
            _print_index_report(report, store)
    finally:
        store.close()
    return 0


def _print_index_report(report: Any, store: GraphStore) -> None:
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
            print(f"\nreferences: {refs['total']:,} total  "
                  f"{refs['exact']:.0%} exact  {refs['inferred']:.0%} inferred  "
                  f"{refs['unresolved']:.0%} unresolved")
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
    cmd.add_argument(
        "--incremental", action="store_true",
        help="also confirm an incremental rebuild equals a cold one",
    )
    cmd.add_argument("--allow-tier-a-only", default="")
    cmd.set_defaults(handler=_run_verify)


def _run_verify(args: Any) -> int:
    """Determinism check (§11, §16.24).

    Two independent builds of the same tree must produce the same digest. A
    grammar upgrade, an unsorted iteration, or a wall-clock value leaking into
    a stored row would all break this -- which is the point of checking rather
    than intending.
    """
    import tempfile

    allow = frozenset(
        part.strip() for part in args.allow_tier_a_only.split(",") if part.strip()
    )
    digests: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for run in (1, 2):
            store, report = Indexer(args.path, allow_tier_a_only=allow).run(
                db_path=Path(tmp) / f"run{run}" / DB_FILENAME
            )
            digests.append(report.build_digest)
            store.close()
            print(f"  build {run}: {report.build_digest}")

    if digests[0] != digests[1]:
        print("\nFAIL: two builds of the same tree differ.", file=sys.stderr)
        print("Something order-dependent or time-dependent reached the store.",
              file=sys.stderr)
        return 1
    print("\nOK: deterministic.")

    if args.incremental:
        print("\nincremental rebuild not yet implemented; cold builds verified")
        return 0
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

    print(f"\n{'language':12s} {'tier A':22s} {'tier B':22s} capabilities")
    for lang, entry in matrix["languages"].items():
        caps = entry.get("capabilities", {})
        marks = "".join(
            "y" if caps.get(name) is True else
            "~" if caps.get(name) == "partial" else
            "?" if caps.get(name) == "untested" else "n"
            for name in ("nodes", "calls", "inherits", "implements",
                         "dataflow", "contracts")
        )
        print(f"{lang:12s} {entry['tier_a']:22s} {entry['tier_b']:22s} {marks}")
    print("            (nodes calls inherits implements dataflow contracts)")

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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
