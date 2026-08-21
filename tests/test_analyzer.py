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
        graph, report = analyze(root, ProjectConfig(source_roots=("src",), test_roots=(), script_roots=()))
        route = graph.symbols["app.api.route"]
        assert route.entry_point == "api_route"
        assert route.async_ is True
        relation = next(item for item in graph.relations if item.source == "app.api.route")
        assert relation.kind == "AWAIT_CALLS"
        assert relation.target == "app.api.calculate"
        assert relation.arguments == [{"parameter": "value", "expression": "value", "inferred_type": "unknown"}]
        assert report.is_clean()  # No failures for valid code
def test_config_driven_risk_rules_and_entry_points():
    from lineagelens.config import AnalysisConfig, RiskRule

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "src" / "app"
        source.mkdir(parents=True)
        (source / "service.py").write_text(
            "def prox(cmd: str):\n    return execute(cmd)\n"
            "async def fe():\n    return await execute('x')\n"
        )
        config = ProjectConfig(
            source_roots=("src",),
            test_roots=(),
            script_roots=(),
            frameworks=("fake_fw",),
            analysis=AnalysisConfig(
                risk_rules=(RiskRule("custom_rule", "critical", ("execute",)),),
                entry_points={"api_route": (".run",)},
            ),
        )
        graph, report = analyze(root, config)
        prox = graph.symbols["app.service.prox"]
        fe = graph.symbols["app.service.fe"]
        # resiliency signals now use Evidence; legacy .risks property still works
        assert len(prox.resiliency) == 1
        assert prox.resiliency[0].category == "custom_rule"
        assert prox.resiliency[0].severity == "critical"
        assert len(fe.resiliency) == 1


def test_config_roundtrip_yaml(tmp_path):
    from lineagelens.config import ProjectConfig

    (tmp_path / "lineagelens.yaml").write_text(
        "source_roots: [lib]\n"
        "test_roots: []\n"
        "script_roots: []\n"
        "frameworks: [django]\n"
        "analysis:\n"
        "  entry_points:\n"
        "    api_route: ['.do']\n"
        "  risk_rules:\n"
        "    - category: pii\n"
        "      severity: high\n"
        "      match_words: [social_security]\n"
        "output:\n"
        "  directory: .artifacts\n"
        "  filename: graph.json\n"
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 9000\n",
        encoding="utf-8",
    )
    config = ProjectConfig.load(tmp_path)
    assert config.source_roots == ("lib",)
    assert config.frameworks == ("django",)
    assert config.analysis.entry_points["api_route"] == (".do",)
    assert config.analysis.risk_rules[0].category == "pii"
    assert config.analysis.risk_rules[0].severity == "high"
    assert config.output.directory == ".artifacts"
    assert config.output.filename == "graph.json"
    assert config.server.port == 9000
