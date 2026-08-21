import tempfile
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig


def test_collects_entry_contract_and_argument_mapping():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "src" / "app"
        source.mkdir(parents=True)
        (source / "api.py").write_text(
            """from fastapi import APIRouter\nrouter = APIRouter()\ndef calculate(value: int) -> int:\n    return value\n@router.get('/value')\nasync def route(value: int):\n    return await calculate(value)\n"""
        )
        graph = analyze(root, ProjectConfig(source_roots=("src",), test_roots=(), script_roots=()))
        route = graph.symbols["app.api.route"]
        assert route.entry_point == "api_route"
        assert route.async_ is True
        relation = next(item for item in graph.relations if item.source == "app.api.route")
        assert relation.kind == "AWAIT_CALLS"
        assert relation.target == "app.api.calculate"
        assert relation.arguments == [{"parameter": "value", "expression": "value", "inferred_type": "unknown"}]
