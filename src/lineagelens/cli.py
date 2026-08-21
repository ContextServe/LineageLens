"""Command-line interface for creating and serving code context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyzer import analyze
from .config import ProjectConfig


CONFIG_TEMPLATE = """source_roots: [src]\ntest_roots: [tests]\nscript_roots: [scripts]\ncron_files: []\ndocs_globs: [\"**/*.md\"]\nframeworks: [fastapi, typer]\nllm:\n  provider: openai_compatible\n  base_url: https://api.openai.com/v1\n  model: gpt-5\n  api_key_env: LINEAGELENS_API_KEY\n"""


def graph_path(project: Path) -> Path:
    return project / ".lineagelens" / "graph.json"


def build(project: Path) -> Path:
    graph = analyze(project, ProjectConfig.load(project))
    target = graph_path(project); target.parent.mkdir(exist_ok=True)
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
        destination.write_text(CONFIG_TEMPLATE, encoding="utf-8"); print(f"Created {destination}"); return
    build(project)
    if args.command == "serve":
        try:
            import uvicorn
            from .web import create_app
        except ImportError as exc:
            raise SystemExit("Install LineageLens with `pip install -e '.[web]'` to serve the UI and GraphQL API.") from exc
        uvicorn.run(create_app(project), host="127.0.0.1", port=8717)


if __name__ == "__main__":
    main()
