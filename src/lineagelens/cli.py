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
from .detect_config import detect_config


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
