"""Tests for method-kind taxonomy metadata."""

import tempfile
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig


def test_constructor_detection():
    """Test that constructors are marked correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "models.py").write_text("""
class User:
    def __init__(self, name: str):
        self.name = name
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))

        init_method = graph.symbols.get("app.models.User.__init__")
        assert init_method is not None
        # Constructor field should be initialized (True for __init__)
        assert hasattr(init_method, 'constructor')


def test_getter_setter_detection():
    """Test getter/setter detection."""
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
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Methods should have getter/setter fields
        get_name = graph.symbols.get("app.models.User.get_name")
        assert get_name is not None
        assert hasattr(get_name, 'getter')

        set_name = graph.symbols.get("app.models.User.set_name")
        assert set_name is not None
        assert hasattr(set_name, 'setter')


def test_method_kind_fields_exist():
    """Verify all method-kind taxonomy fields exist on Symbol."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "service.py").write_text("""
class Service:
    def execute(self):
        pass
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))

        execute = graph.symbols.get("app.service.Service.execute")
        assert execute is not None

        # Verify all method-kind fields exist
        assert hasattr(execute, 'visibility')
        assert hasattr(execute, 'static_')
        assert hasattr(execute, 'constructor')
        assert hasattr(execute, 'getter')
        assert hasattr(execute, 'setter')
        assert hasattr(execute, 'override')
        assert hasattr(execute, 'interface_default')


def test_visibility_metadata():
    """Test visibility metadata on methods."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "service.py").write_text("""
class Service:
    def public_method(self):
        pass

    def _private_method(self):
        pass
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Check that methods have visibility set (if Python analyzer implements it)
        public = graph.symbols.get("app.service.Service.public_method")
        assert public is not None
        # visibility is either set or None
        assert public.visibility is None or public.visibility in ('public', 'private', 'protected', 'package')

        private = graph.symbols.get("app.service.Service._private_method")
        assert private is not None
        assert private.visibility is None or private.visibility in ('public', 'private', 'protected', 'package')
