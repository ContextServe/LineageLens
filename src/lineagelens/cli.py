"""Command-line interface for creating and serving code context."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from .analyzer import analyze
from .config import ProjectConfig


def _plain(value: Any) -> Any:
    """Recursively convert tuples (from dataclass asdict) into YAML-friendly lists."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def config_template() -> str:
    return yaml.safe_dump(_plain(asdict(ProjectConfig())), sort_keys=False)


def graph_path(project: Path, config: ProjectConfig) -> Path:
    return project / config.output.directory / config.output.filename


def report_path(project: Path, config: ProjectConfig) -> Path:
    return project / config.output.directory / "report.json"


def build(project: Path) -> tuple[Path, Path]:
    """Analyze project and write graph.json + report.json.

    Returns:
        (graph_path, report_path) tuple
    """
    config = ProjectConfig.load(project)
    graph, report = analyze(project, config)

    # Write graph.json
    graph_file = graph_path(project, config)
    graph_file.parent.mkdir(parents=True, exist_ok=True)
    graph_file.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")

    # Write report.json
    report_file = report_path(project, config)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    # Print human-readable summary
    for line in report.summary_lines():
        print(line)

    return graph_file, report_file


def main() -> None:
    parser = argparse.ArgumentParser(prog="lineagelens", description="Evidence-labelled Python code lineage for humans and coding agents")
    commands = parser.add_subparsers(dest="command", required=True, help="Command to run")

    init_cmd = commands.add_parser("init", help="Initialize lineagelens.yaml")
    init_cmd.add_argument("project", type=Path, help="Project directory")

    analyze_cmd = commands.add_parser("analyze", help="Analyze Python code and build graph")
    analyze_cmd.add_argument("project", type=Path, help="Project directory")
    analyze_cmd.add_argument("--strict", action="store_true", help="Exit with nonzero code if any failures occur")

    serve_cmd = commands.add_parser("serve", help="Start local web UI and GraphQL server")
    serve_cmd.add_argument("project", type=Path, help="Project directory")

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
        destination.write_text(config_template(), encoding="utf-8")
        print(f"Created {destination}")
        return

    # analyze and serve commands
    config = ProjectConfig.load(project)
    graph_file, report_file = build(project)

    if args.command == "serve":
        try:
            import uvicorn

            from .web import create_app
        except ImportError as exc:
            raise SystemExit("Install LineageLens with `pip install -e '.[web]'` to serve the UI and GraphQL API.") from exc
        uvicorn.run(create_app(project, config), host=config.server.host, port=config.server.port)

    # Check for failures if --strict is set
    if args.command == "analyze" and args.strict:
        import json
        report = json.loads(report_file.read_text())
        if report.get("failures"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
