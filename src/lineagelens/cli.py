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


def build(project: Path) -> Path:
    config = ProjectConfig.load(project)
    graph = analyze(project, config)
    target = graph_path(project, config); target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(graph.to_dict(), indent=2), encoding="utf-8")
    print(f"Wrote {len(graph.symbols)} symbols and {len(graph.relations)} relations to {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(prog="lineagelens")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "analyze", "serve"):
        item = commands.add_parser(name)
        item.add_argument("project", type=Path)
    args = parser.parse_args(); project = args.project.resolve()
    if args.command == "init":
        destination = project / "lineagelens.yaml"
        if destination.exists(): parser.error(f"{destination} already exists")
        destination.write_text(config_template(), encoding="utf-8"); print(f"Created {destination}"); return
    config = ProjectConfig.load(project)
    build(project)
    if args.command == "serve":
        try:
            import uvicorn

            from .web import create_app
        except ImportError as exc:
            raise SystemExit("Install LineageLens with `pip install -e '.[web]'` to serve the UI and GraphQL API.") from exc
        uvicorn.run(create_app(project, config), host=config.server.host, port=config.server.port)


if __name__ == "__main__":
    main()
