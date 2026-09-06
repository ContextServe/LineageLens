"""Tests for method-kind taxonomy metadata."""

import tempfile
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig


def test_constructor_detection():
    """Test that __init__ is marked as constructor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "models.py").write_text("""
class User:
    def __init__(self, name: str):
        self.name = name

    def display(self):
        return self.name
""")

        graph, _report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Constructor check
        init_method = graph.symbols.get("app.models.User.__init__")
        assert init_method is not None
        assert init_method.constructor is True, "Expected __init__ to have constructor=True"

        # Non-constructor check
        display = graph.symbols.get("app.models.User.display")
        assert display is not None
        assert display.constructor is False, "Expected display() to have constructor=False"


def test_getter_setter_detection():
    """Test getter/setter detection via naming pattern."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "models.py").write_text("""
class User:
    def get_name(self):
        return self._name

    def set_name(self, value):
        self._name = value

    def is_active(self):
        return self._active
""")

        graph, _report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Getter detection (get_* pattern)
        get_name = graph.symbols.get("app.models.User.get_name")
        assert get_name is not None
        assert get_name.getter is True, "Expected get_name() to be detected as getter"
        assert get_name.setter is False

        # Setter detection (set_* pattern)
        set_name = graph.symbols.get("app.models.User.set_name")
        assert set_name is not None
        assert set_name.setter is True, "Expected set_name() to be detected as setter"
        assert set_name.getter is False

        # Predicate getter (is_* pattern)
        is_active = graph.symbols.get("app.models.User.is_active")
        assert is_active is not None
        assert is_active.getter is True, "Expected is_active() to be detected as getter"


def test_method_kind_fields_complete():
    """Verify all method-kind taxonomy fields are properly initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "service.py").write_text("""
class Service:
    def execute(self):
        pass
""")

        graph, _report = analyze(root, ProjectConfig(source_roots=("src",)))

        execute = graph.symbols.get("app.service.Service.execute")
        assert execute is not None

        # Verify all method-kind fields exist and are set appropriately
        assert hasattr(execute, 'visibility')
        assert hasattr(execute, 'static_')
        assert hasattr(execute, 'constructor')
        assert hasattr(execute, 'getter')
        assert hasattr(execute, 'setter')
        assert hasattr(execute, 'override')

        # Normal method should have defaults
        assert execute.constructor is False
        assert execute.getter is False
        assert execute.setter is False
        assert execute.override is False


def test_naming_patterns_not_confused():
    """Test that get/set patterns only apply to methods, not other attributes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "service.py").write_text("""
class Service:
    def get_value_and_store(self):
        pass

    def setter_upper_case(self):
        pass
""")

        graph, _report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Method starting with get_ should be getter
        get_value = graph.symbols.get("app.service.Service.get_value_and_store")
        assert get_value is not None
        assert get_value.getter is True

        # Method starting with set* but not matching set_*  pattern should not be setter
        setter_upper = graph.symbols.get("app.service.Service.setter_upper_case")
        assert setter_upper is not None
        # setter_upper_case starts with "set" but not "set_" so shouldn't match set pattern
        assert setter_upper.setter is False
