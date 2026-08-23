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

    report_file = report_path(project, config)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    if not quiet:
        for line in report.summary_lines():
            print(line)

    return graph_file, report_file


def build(
    project: Path, quiet: bool = False, jedi: bool | None = None
) -> tuple[Path, Path, AnalysisReport]:
    """Analyze a project and write graph.json + report.json.

    Returns:
        ``(graph_path, report_path, report)``. The in-memory report is returned so
        that ``--strict`` can inspect it directly rather than re-reading and
        re-parsing the file it just wrote.
    """
    config = ProjectConfig.load(project)
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="lineagelens", description="Evidence-labelled Python code lineage for humans and coding agents")
    commands = parser.add_subparsers(dest="command", required=True, help="Command to run")

    init_cmd = commands.add_parser("init", help="Initialize lineagelens.yaml")
    init_cmd.add_argument("project", type=Path, help="Project directory")

    analyze_cmd = commands.add_parser("analyze", help="Analyze Python code and build graph")
    analyze_cmd.add_argument("project", type=Path, help="Project directory")
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

    args = parser.parse_args()
    project = args.project.resolve()

    # Validate project path exists
    if not project.exists():
        parser.error(f"Project directory does not exist: {project}")
    if not project.is_dir():
        parser.error(f"Not a directory: {project}")

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
