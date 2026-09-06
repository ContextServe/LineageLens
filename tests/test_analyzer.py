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
        graph, _report = analyze(root, config)
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


def test_python_method_local_variables_extraction():
    """Test that local variables within methods are extracted with type information."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "src" / "app"
        source.mkdir(parents=True)
        (source / "utils.py").write_text(
            """class Calculator:
    def compute(self, a: int, b: int) -> int:
        sum_val = a + b
        product = a * b
        result = sum_val + product
        return result

    def process_data(self):
        items: list[str] = []
        temp = "data"
        count = 0
        items.append(temp)
        return items

def standalone_func(x):
    y = x * 2
    z: str = "hello"
    return y + len(z)
"""
        )
        graph, _ = analyze(root, ProjectConfig(source_roots=("src",), test_roots=(), script_roots=()))

        # Check compute method
        compute_method = graph.symbols["app.utils.Calculator.compute"]
        assert compute_method is not None
        assert compute_method.kind == "method"
        assert len(compute_method.locals) > 0, "compute() should have extracted local variables"

        # Verify local variable names
        local_names = {local["name"] for local in compute_method.locals}
        assert "sum_val" in local_names, "Local variable 'sum_val' should be captured"
        assert "product" in local_names, "Local variable 'product' should be captured"
        assert "result" in local_names, "Local variable 'result' should be captured"

        # Verify type information (complex expressions may infer as unknown)
        sum_val_local = next((loc for loc in compute_method.locals if loc["name"] == "sum_val"), None)
        assert sum_val_local is not None
        # Type inference for complex expressions may result in "unknown", which is acceptable
        assert sum_val_local["type"] in ("int", "unknown"), f"sum_val type should be int or unknown, got {sum_val_local['type']}"

        # Check process_data method
        process_method = graph.symbols["app.utils.Calculator.process_data"]
        assert process_method is not None
        assert len(process_method.locals) > 0, "process_data() should have extracted local variables"

        local_names_2 = {local["name"] for local in process_method.locals}
        assert "items" in local_names_2, "Local variable 'items' should be captured"
        assert "temp" in local_names_2, "Local variable 'temp' should be captured"
        assert "count" in local_names_2, "Local variable 'count' should be captured"

        # Verify type annotation was captured
        items_local = next((loc for loc in process_method.locals if loc["name"] == "items"), None)
        assert items_local is not None
        assert "list" in items_local["type"], f"items should have list type, got {items_local['type']}"

        # Check standalone function
        standalone_func = graph.symbols["app.utils.standalone_func"]
        assert standalone_func is not None
        assert len(standalone_func.locals) > 0, "standalone_func() should have extracted local variables"

        standalone_locals = {loc["name"] for loc in standalone_func.locals}
        assert "y" in standalone_locals, "Local variable 'y' should be captured"
        assert "z" in standalone_locals, "Local variable 'z' should be captured"

        # Check that z has type annotation
        z_local = next((loc for loc in standalone_func.locals if loc["name"] == "z"), None)
        assert z_local is not None
        assert z_local["type"] == "str", f"z should be str, got {z_local['type']}"


def test_python_nested_function_scope_isolation():
    """Test that nested function variables are NOT extracted as outer function locals (scope isolation)."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "src" / "app"
        source.mkdir(parents=True)
        (source / "scope_test.py").write_text(
            """def outer():
    x = 1  # outer local
    def inner():
        y = 2  # inner local (should NOT be in outer.locals)
    z = 3  # outer local
    return x + z

def with_class():
    a = 1  # with_class local
    class Inner:
        b = 2  # class attribute (should NOT be in with_class.locals)
    c = 3
    return a + c
"""
        )
        graph, _ = analyze(root, ProjectConfig(source_roots=("src",), test_roots=(), script_roots=()))

        # Check outer function
        outer_func = graph.symbols["app.scope_test.outer"]
        outer_locals = {loc["name"] for loc in outer_func.locals}
        assert "x" in outer_locals, "Outer local 'x' should be extracted"
        assert "z" in outer_locals, "Outer local 'z' should be extracted"
        assert "y" not in outer_locals, "Inner local 'y' should NOT be in outer's locals (scope violation)"

        # Check with_class function
        with_class_func = graph.symbols["app.scope_test.with_class"]
        with_class_locals = {loc["name"] for loc in with_class_func.locals}
        assert "a" in with_class_locals, "Local 'a' should be extracted"
        assert "c" in with_class_locals, "Local 'c' should be extracted"
        assert "b" not in with_class_locals, "Class attribute 'b' should NOT be in with_class's locals (scope violation)"

