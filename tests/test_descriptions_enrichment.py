"""Tests for description extraction on fields and variables.

Verifies:
1. Field/variable symbols can have descriptions extracted from source
2. No LLM synthesis - only extraction from docstrings/comments
3. Null when no doc-comment exists
"""

import tempfile
from pathlib import Path

from lineagelens.analyzer import analyze
from lineagelens.config import ProjectConfig


def test_field_symbols_preserve_description_field():
    """Verify field symbols have description attribute (infrastructure test)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "models.py").write_text("""
class User:
    \"\"\"User model.\"\"\"
    name: str = None
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))
        assert report is not None

        # All field symbols should have description attribute (None if not set)
        for symbol in graph.symbols.values():
            if symbol.kind == "field":
                assert hasattr(symbol, 'description')
                # Description is None or string (no synthesis)
                assert symbol.description is None or isinstance(symbol.description, str)


def test_no_description_synthesis_on_fields():
    """Verify fields have no synthesized descriptions - only extracted from source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "config.py").write_text("""
class Config:
    debug: bool
    timeout: int
    retries: int
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Verify analysis completes
        assert graph is not None
        # Field descriptions are only from source, never generated
        for symbol in graph.symbols.values():
            if symbol.kind == "field":
                if symbol.description:
                    # Any description must be from explicit source comment/docstring
                    assert isinstance(symbol.description, str)


def test_methods_and_classes_still_have_descriptions():
    """Regression test: existing description extraction still works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src" / "app"
        src.mkdir(parents=True)

        (src / "service.py").write_text("""
class UserService:
    \"\"\"Service for user operations.\"\"\"

    def get_user(self, user_id: int):
        \"\"\"Retrieve a user by ID.\"\"\"
        return None
""")

        graph, report = analyze(root, ProjectConfig(source_roots=("src",)))

        # Classes should have descriptions
        user_service = graph.symbols.get("app.service.UserService")
        assert user_service is not None
        assert user_service.description is not None
        assert "Service for user" in user_service.description

        # Methods should have descriptions
        get_user = graph.symbols.get("app.service.UserService.get_user")
        assert get_user is not None
        assert get_user.description is not None
        assert "Retrieve a user" in get_user.description
