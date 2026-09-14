"""Command-line interface for creating and serving code context."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import yaml

from . import hooks
from .analyzer import analyze
from .config import ProjectConfig
from .detect_config import detect_config
from .model import CodeGraph
from .queries import GraphNotFoundError, load_graph
from .ratchet import BASELINE_NAME, SEVERITY, Baseline, evaluate, severity_at_or_above
from .reachability import compute_reachability
from .report import AnalysisReport


def _plain(value: Any) -> Any:
    """Recursively convert tuples (from dataclass asdict) into YAML-friendly lists."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def config_template(project: Path | None = None) -> tuple[str, list[str]]:
    """Generate config template, optionally auto-detecting values.

    Args:
        project: Project directory for auto-detection (if None, uses defaults)

    Returns:
        (yaml_content, notes) tuple where notes are human-readable detection messages
    """
    if project and project.exists():
        detection = detect_config(project)
        config_dict = _plain(asdict(ProjectConfig(
            source_roots=tuple(detection.source_roots),
            test_roots=tuple(detection.test_roots),
            frameworks=tuple(detection.frameworks),
        )))
        notes = detection.notes
    else:
        config_dict = _plain(asdict(ProjectConfig()))
        notes = []

    yaml_content = yaml.safe_dump(config_dict, sort_keys=False)
    return yaml_content, notes


def graph_path(project: Path, config: ProjectConfig) -> Path:
    return project / config.output.directory / config.output.filename


def report_path(project: Path, config: ProjectConfig) -> Path:
    return project / config.output.directory / "report.json"


def check_frontend_available() -> None:
    """Check if frontend is available. Provide guidance if not."""
    from .web import locate_frontend_dist

    dist_dir = locate_frontend_dist()
    if dist_dir:
        return  # Frontend is ready

    # Frontend not found — provide guidance
    print("⚠️  Frontend UI not found.")
    print()
    print("To build the frontend locally, run:")
    print("  cd frontend && npm install && npm run build")
    print()
    print("Alternatively, install a released version of lineagelens which includes")
    print("the prebuilt frontend:")
    print("  pip install lineagelens[web]  # from PyPI")
    print()
    print("For now, the REST API and GraphQL are still available at:")
    print("  http://127.0.0.1:8717/api/v1")
    print("  http://127.0.0.1:8717/graphql")


def write_artifacts(
    project: Path,
    config: ProjectConfig,
    graph: CodeGraph,
    report: AnalysisReport,
    quiet: bool = False,
) -> tuple[Path, Path]:
    """Persist an already-computed graph and report.

    Split out of :func:`build` so that callers holding a graph in memory (the REST
    and MCP trigger endpoints) can write it without analysing a second time.

    Returns:
        ``(graph_path, report_path)``
    """
    graph_file = graph_path(project, config)
    graph_file.parent.mkdir(parents=True, exist_ok=True)
    graph_file.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")

    db_file = project / config.output.directory / "index.sqlite"
    try:
        from .db import SQLiteIndexDB
        db = SQLiteIndexDB(db_file)
        db.save_code_graph(graph)
    except Exception as e:
        print(f"⚠️  Could not populate index.sqlite: {e}")

    report_file = report_path(project, config)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    if not quiet:
        for line in report.summary_lines():
            print(line)

    return graph_file, report_file


def build(
    project: Path, quiet: bool = False, jedi: bool | None = None, engine: str | None = None
) -> tuple[Path, Path, AnalysisReport]:
    """Analyze a project and write graph.json + report.json.

    Returns:
        ``(graph_path, report_path, report)``.
    """
    from .detect_config import is_java_project, is_js_project
    from .java_bridge import run_java_analysis
    from .js_bridge import run_js_analysis

    config = ProjectConfig.load(project)
    if engine is not None:
        config = replace(config, analysis=replace(config.analysis, engine=engine))

    # Check if target project is JS/TS and using default compiler engine
    if config.analysis.engine == "compiler" and (is_js_project(project) or getattr(config, "language", None) in ("javascript", "typescript", "js", "ts")):
        graph_file = run_js_analysis(project)
        report_file = report_path(project, config)
        report = AnalysisReport(
            project_root=str(project),
            started_at="",
            finished_at="",
            files_scanned=1,
            files_skipped=0,
            symbols_found=0,
            relations_found=0,
            containers_found=0,
        )
        if not quiet:
            print(f"✓ JavaScript/TypeScript analysis complete. Graph written to {graph_file}")
        return graph_file, report_file, report

    # Check if target project is Java and using default compiler engine
    if config.analysis.engine == "compiler" and (is_java_project(project) or getattr(config, "language", None) == "java"):
        graph_file = run_java_analysis(project)
        report_file = report_path(project, config)
        report = AnalysisReport(
            project_root=str(project),
            started_at="",
            finished_at="",
            files_scanned=1,
            files_skipped=0,
            symbols_found=0,
            relations_found=0,
            containers_found=0,
        )
        if not quiet:
            print(f"✓ Java analysis complete. Graph written to {graph_file}")
        return graph_file, report_file, report

    if jedi is not None and jedi != config.analysis.jedi:
        config = replace(config, analysis=replace(config.analysis, jedi=jedi))
    graph, report = analyze(project, config)
    graph_file, report_file = write_artifacts(project, config, graph, report, quiet=quiet)
    return graph_file, report_file, report



def run_check(project: Path, args: Any) -> int:
    """Compare current dead-code candidates against the baseline. Returns an exit code."""
    config = ProjectConfig.load(project)

    if args.analyze:
        graph, report = analyze(project, config)
        write_artifacts(project, config, graph, report, quiet=True)
    else:
        try:
            graph = load_graph(project)
        except GraphNotFoundError as e:
            print(f"✗ {e}")
            return 1

    candidates = compute_reachability(graph, config).candidates()
    baseline_path = args.baseline or (project / config.output.directory / BASELINE_NAME)

    if args.update_baseline:
        considered = severity_at_or_above(args.fail_on)
        updated = Baseline(
            entries={c.symbol.id: c.verdict for c in candidates if c.verdict in considered}
        )
        updated.save(baseline_path)
        print(f"✓ Baseline written to {baseline_path} ({len(updated.entries)} entries)")
        return 0

    result = evaluate(candidates, Baseline.load(baseline_path), fail_on=args.fail_on)
    for line in result.summary_lines():
        print(line)

    if result.failed(args.max_new):
        print(
            "\n✗ New dead code introduced. Remove it, or -- if it is reached by "
            "reflection or config-driven dispatch that static analysis cannot see -- "
            "mark it `# lineagelens: keep` and re-run with --update-baseline."
        )
        return 1
    return 0



def _run_auth(args: Any) -> None:  # noqa: C901 — intentionally a command dispatch
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
        env = args.env

        if not args.reauth and store.is_token_valid(env):
            creds = store.get(env)
            print()
            print(f"  ✓ Already logged in as {creds['email']} ({env})")
            print(f"  Token valid until: {creds['expires_at']}")
            print()
            print("  To re-authenticate:          lineagelens auth login --reauth")
            print(f"  To login to another env:     lineagelens auth login --env <env>")
            return

        try:
            token_resp = run_login(
                env=env,
                no_browser=args.no_browser,
                timeout=args.timeout,
            )
        except LoginTimeout as exc:
            raise SystemExit(f"\n  ✗ {exc}") from exc
        except LoginRejected as exc:
            raise SystemExit(f"\n  ✗ {exc}") from exc
        except LoginError as exc:
            raise SystemExit(f"\n  ✗ {exc}") from exc

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
                        headers={"Authorization": f"Bearer {token_resp['access_token']}"},
                    )
                    if me.is_success:
                        email = me.json().get("email", "")
                        store.save(env, token_resp, base_url, email=email)
            except Exception:
                pass

        print()
        print(f"  ✓ Logged in as {email or '(unknown)'}")
        print(f"  ✓ Environment : {env}")
        print(f"  ✓ Organization: {token_resp.get('organization_name', '')}"
              f"  ({token_resp.get('plan_tier', '')})")
        creds = store.get(env)
        if creds:
            print(f"  ✓ Token valid until: {creds['expires_at']}")
        print()
        print("  Next steps:")
        print(f"    lineagelens auth switch-env prod   # switch environment")
        print(f"    lineagelens auth status            # view all sessions")

    # ── logout ────────────────────────────────────────────────────────────────
    elif sub == "logout":
        if args.all:
            store.remove_all()
            print("  ✓ Logged out of all environments.")
        else:
            env = args.env
            removed = store.remove(env)
            if removed:
                print(f"  ✓ Logged out of '{env}'.")
            else:
                print(f"  • No credentials found for '{env}'.")

    # ── status ────────────────────────────────────────────────────────────────
    elif sub == "status":
        all_creds = store.all_envs()
        active = store.active_env
        all_envs = ["prod", "stage", "dev", "local"]

        print()
        print(f"  {'Environment':<10}  {'User':<30}  {'Status':<8}  Expires")
        print(f"  {'─' * 10}  {'─' * 30}  {'─' * 8}  {'─' * 20}")
        for env in all_envs:
            creds = all_creds.get(env)
            marker = "✓" if creds else "✗"
            user_col = creds["email"] if creds else "(not authenticated)"
            status_col = "active" if (creds and store.is_token_valid(env)) else ("expired" if creds else "")
            expires_col = creds.get("expires_at", "") if creds else ""
            active_marker = " ◀" if env == active else ""
            print(f"  {env:<10}  {marker} {user_col:<28}  {status_col:<8}  {expires_col}{active_marker}")

        print()
        print(f"  Active environment: {active}")
        print()

    # ── token ─────────────────────────────────────────────────────────────────
    elif sub == "token":
        env = args.env
        creds = store.get(env)
        if not creds or not creds.get("access_token"):
            raise SystemExit(
                f"✗ Not authenticated for '{env}'. "
                f"Run: lineagelens auth login --env {env}"
            )
        # Write only the raw token — no trailing newline noise for scripts
        import sys
        print(creds["access_token"], end="")
        sys.stdout.flush()

    # ── switch-env ────────────────────────────────────────────────────────────
    elif sub == "switch-env":
        env = args.env
        store.set_active_env(env)
        creds = store.get(env)
        if creds:
            print(f"  ✓ Active environment set to '{env}' ({creds['email']})")
        else:
            print(f"  ✓ Active environment set to '{env}'")
            print(f"  ⚠  Not yet authenticated. Run: lineagelens auth login --env {env}")


def main() -> None:

    parser = argparse.ArgumentParser(prog="lineagelens", description="Evidence-labelled Python code lineage for humans and coding agents")
    commands = parser.add_subparsers(dest="command", required=True, help="Command to run")

    init_cmd = commands.add_parser("init", help="Initialize lineagelens.yaml")
    init_cmd.add_argument("project", type=Path, help="Project directory")

    analyze_cmd = commands.add_parser("analyze", help="Analyze Python code and build graph")
    analyze_cmd.add_argument("project", type=Path, help="Project directory")
    analyze_cmd.add_argument(
        "--engine",
        choices=["compiler", "tree-sitter", "scip", "hybrid"],
        default=None,
        help="AST analysis engine (default: compiler or value from lineagelens.yaml)",
    )
    analyze_cmd.add_argument("--strict", action="store_true", help="Exit with nonzero code if any failures occur")

    analyze_cmd.add_argument(
        "--no-jedi",
        action="store_true",
        help="Skip type inference. Faster, resolves fewer dynamic calls (useful on PR-time CI runs)",
    )
    analyze_cmd.add_argument("--quiet", action="store_true", help="Do not print the analysis summary")

    serve_cmd = commands.add_parser("serve", help="Start local web UI and GraphQL server")
    serve_cmd.add_argument("project", type=Path, help="Project directory")

    check_cmd = commands.add_parser(
        "check",
        help="Fail on newly introduced dead code (CI ratchet)",
        description=(
            "Compare dead-code candidates against a committed baseline and exit 1 only "
            "on ones that are new. A ratchet is adoptable on day one, where an absolute "
            "gate would fail every existing codebase and get switched off."
        ),
    )
    check_cmd.add_argument("project", type=Path, help="Project directory")
    check_cmd.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Baseline file (default: <output dir>/dead-code-baseline.json)",
    )
    check_cmd.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline from the current state and exit 0",
    )
    check_cmd.add_argument(
        "--fail-on",
        choices=list(SEVERITY),
        default="dead",
        help="Least severe verdict that should fail (default: dead)",
    )
    check_cmd.add_argument(
        "--max-new",
        type=int,
        default=0,
        help="Tolerate this many new candidates before failing (default: 0)",
    )
    check_cmd.add_argument(
        "--analyze",
        action="store_true",
        help="Re-run the analysis first instead of using the stored graph",
    )

    hook_cmd = commands.add_parser("hook", help="Manage the git hook that keeps the graph current")
    hook_cmd.add_argument("action", choices=["install", "uninstall", "status"])
    hook_cmd.add_argument("project", type=Path, nargs="?", default=Path("."), help="Project directory")
    hook_cmd.add_argument(
        "--pre-commit",
        action="store_true",
        help="Use pre-commit instead of post-commit (adds seconds to every commit)",
    )
    hook_cmd.add_argument(
        "--force", action="store_true", help="Append to a hook LineageLens did not create"
    )

    watch_cmd = commands.add_parser("watch", help="Run background watcher daemon to update index on file save")
    watch_cmd.add_argument("project", type=Path, nargs="?", default=Path("."), help="Project directory")
    watch_cmd.add_argument("--daemon", action="store_true", help="Run as detached background daemon")
    watch_cmd.add_argument("--stop", action="store_true", help="Stop running background daemon")
    watch_cmd.add_argument("--status", action="store_true", help="Check running status of background watcher daemon")

    sync_cmd = commands.add_parser("sync", help="Incrementally sync a single file into SQLite index")
    sync_cmd.add_argument("file", type=str, help="Relative path to file to sync")
    sync_cmd.add_argument("project", type=Path, nargs="?", default=Path("."), help="Project directory")

    # ── auth ──────────────────────────────────────────────────────────────────
    _envs = ["prod", "stage", "dev", "local"]
    auth_cmd = commands.add_parser("auth", help="Authenticate with ContextServe.ai")
    auth_sub = auth_cmd.add_subparsers(dest="auth_command", required=True)

    login_p = auth_sub.add_parser("login", help="Log in via browser + one-time code")
    login_p.add_argument("--env", "-e", default="prod", choices=_envs,
                         help="Target environment (default: prod)")
    login_p.add_argument("--no-browser", action="store_true",
                         help="Print URL instead of opening browser (useful over SSH)")
    login_p.add_argument("--reauth", action="store_true",
                         help="Re-authenticate even if a valid token already exists")
    login_p.add_argument("--timeout", type=int, default=300,
                         help="Seconds to wait for browser login (default: 300)")

    logout_p = auth_sub.add_parser("logout", help="Revoke and remove stored credentials")
    logout_p.add_argument("--env", "-e", default="prod", choices=_envs)
    logout_p.add_argument("--all", action="store_true", help="Log out of all environments")

    auth_sub.add_parser("status", help="Show authentication status for all environments")

    token_p = auth_sub.add_parser("token", help="Print raw bearer token to stdout")
    token_p.add_argument("--env", "-e", default="prod", choices=_envs)

    switch_p = auth_sub.add_parser("switch-env", help="Change active environment")
    switch_p.add_argument("env", choices=_envs)

    args = parser.parse_args()

    # ── auth — handled before project-path validation (no project dir needed) ──
    if args.command == "auth":
        _run_auth(args)
        return

    project = args.project.resolve()

    # Validate project path exists
    if not project.exists():
        parser.error(f"Project directory does not exist: {project}")
    if not project.is_dir():
        parser.error(f"Not a directory: {project}")


    if args.command == "watch":
        from .watcher import LineageLensWatcher
        watcher = LineageLensWatcher(project)
        if args.stop:
            stopped = watcher.stop()
            if stopped:
                print(f"✓ Stopped LineageLens watcher for {project}")
            else:
                print("LineageLens watcher is not currently running.")
            return
        if args.status:
            st = watcher.get_status()
            if st["running"]:
                print(f"✓ LineageLens watcher active (PID {st['pid']})")
            else:
                print("• LineageLens watcher is not running.")
            return

        print(f"🚀 Starting LineageLens watcher daemon for {project}...")
        watcher.start(daemon=args.daemon)
        return

    if args.command == "sync":
        from .db import DB_NAME, SQLiteIndexDB
        from .incremental import IncrementalAnalyzer
        db = SQLiteIndexDB(project / ".lineagelens" / DB_NAME)
        analyzer = IncrementalAnalyzer()
        ok = analyzer.sync_file(project, args.file, db)
        if ok:
            print(f"✓ Incremental sync complete: {args.file}")
        else:
            print(f"• File skipped or failed to sync: {args.file}")
        return

    if args.command == "init":
        destination = project / "lineagelens.yaml"
        if destination.exists():
            parser.error(f"{destination} already exists")
        yaml_content, notes = config_template(project)
        destination.write_text(yaml_content, encoding="utf-8")

        # Print summary
        print(f"✓ Created {destination}\n")

        if notes:
            print("Auto-detection results:")
            for note in notes:
                print(f"  {note}")

        # Parse the YAML to show what was written
        config = ProjectConfig.load(project, destination)
        print("\nConfiguration written:")
        print(f"  source_roots: {list(config.source_roots)}")
        print(f"  test_roots: {list(config.test_roots)}")
        print(f"  frameworks: {list(config.frameworks)}")

        print("\nNext steps:")
        print("  1. Review the configuration in lineagelens.yaml")
        print("  2. Run: lineagelens analyze .")
        print("  3. Optionally run: lineagelens serve . (for web UI)")

        return

    if args.command == "hook":
        kind = "pre-commit" if args.pre_commit else "post-commit"
        try:
            if args.action == "install":
                path = hooks.install(project, kind=kind, force=args.force)
                print(f"✓ Installed {kind} hook at {path}")
                if kind == "pre-commit":
                    print("  Note: this runs on every commit and will add a few seconds each time.")
                print("\n  Add .lineagelens/ to .gitignore -- the graph is a build artifact,")
                print("  not source. Publish it from CI if agents need it centrally.")
            elif args.action == "uninstall":
                removed = hooks.uninstall(project, kind=kind)
                print(f"✓ Removed the managed block from the {kind} hook" if removed
                      else f"No LineageLens block found in the {kind} hook")
            else:
                for name, present in hooks.status(project).items():
                    print(f"  {name:12s} {'installed' if present else '-'}")
        except hooks.HookError as e:
            raise SystemExit(f"✗ {e}") from e
        return

    if args.command == "check":
        raise SystemExit(run_check(project, args))

    # analyze and serve commands
    config = ProjectConfig.load(project)
    _graph_file, _report_file, analysis_report = build(
        project,
        quiet=getattr(args, "quiet", False),
        jedi=False if getattr(args, "no_jedi", False) else None,
        engine=getattr(args, "engine", None),
    )


    if args.command == "serve":
        # Check if frontend is available (provide guidance if not, but don't block)
        check_frontend_available()

        try:
            import uvicorn

            from .web import create_app
        except ImportError as exc:
            raise SystemExit("Install LineageLens with `pip install -e '.[web]'` to serve the UI and GraphQL API.") from exc

        print(f"\n🚀 Starting LineageLens server at http://{config.server.host}:{config.server.port}")
        print("   Press Ctrl+C to stop\n")
        uvicorn.run(create_app(project, config), host=config.server.host, port=config.server.port)

    # Check for failures if --strict is set
    if (
        args.command == "analyze"
        and args.strict
        and analysis_report is not None
        and analysis_report.has_failures()
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
