"""Tests for schema version validation and backward compatibility."""

import json
import tempfile
from pathlib import Path

import pytest

from lineagelens.model import SCHEMA_VERSION
from lineagelens.queries import GraphNotFoundError, load_graph


def test_schema_version_is_3():
    """Verify SCHEMA_VERSION constant is 3."""
    assert SCHEMA_VERSION == 3


def test_load_graph_v3_succeeds():
    """Test loading a v3 graph succeeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        graph_dir = project_root / ".lineagelens"
        graph_dir.mkdir(parents=True)

        # Create a minimal v3 graph
        v3_graph = {
            "project_root": str(project_root),
            "schema_version": 3,
            "containers": [],
            "symbols": {},
            "relations": [],
            "evidence_index": [],
        }

        graph_file = graph_dir / "graph.json"
        graph_file.write_text(json.dumps(v3_graph))

        # Should load without error
        graph = load_graph(project_root)
        assert graph is not None
        assert graph.project_root == str(project_root)


def test_load_graph_v2_raises_error():
    """Test loading a v2 graph raises GraphNotFoundError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        graph_dir = project_root / ".lineagelens"
        graph_dir.mkdir(parents=True)

        # Create a minimal v2 graph
        v2_graph = {
            "project_root": str(project_root),
            "schema_version": 2,  # Old version
            "containers": [],
            "symbols": {},
            "relations": [],
            "evidence_index": [],
        }

        graph_file = graph_dir / "graph.json"
        graph_file.write_text(json.dumps(v2_graph))

        # Should raise error
        with pytest.raises(GraphNotFoundError) as exc_info:
            load_graph(project_root)

        # Verify error message mentions version mismatch
        assert "schema" in str(exc_info.value).lower()
        assert "found v2" in str(exc_info.value)
        assert "expected v3" in str(exc_info.value)


def test_load_graph_v1_raises_error():
    """Test loading a v1 graph raises GraphNotFoundError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        graph_dir = project_root / ".lineagelens"
        graph_dir.mkdir(parents=True)

        # Create a minimal v1 graph (no schema_version field)
        v1_graph = {
            "project_root": str(project_root),
            # No schema_version = defaults to 1
            "containers": [],
            "symbols": {},
            "relations": [],
        }

        graph_file = graph_dir / "graph.json"
        graph_file.write_text(json.dumps(v1_graph))

        # Should raise error
        with pytest.raises(GraphNotFoundError) as exc_info:
            load_graph(project_root)

        assert "schema" in str(exc_info.value).lower()


def test_schema_mismatch_error_message_helpful():
    """Test error message guides user to solution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        graph_dir = project_root / ".lineagelens"
        graph_dir.mkdir(parents=True)

        v2_graph = {
            "project_root": str(project_root),
            "schema_version": 2,
            "containers": [],
            "symbols": {},
            "relations": [],
            "evidence_index": [],
        }

        graph_file = graph_dir / "graph.json"
        graph_file.write_text(json.dumps(v2_graph))

        try:
            load_graph(project_root)
            pytest.fail("Should have raised GraphNotFoundError")
        except GraphNotFoundError as e:
            error_msg = str(e)
            # Verify error message tells user to re-run analyze
            assert "lineagelens analyze" in error_msg
            # Verify it explains the issue
            assert "different LineageLens graph schema" in error_msg


def test_graph_without_schema_version_defaults_to_1():
    """Test graph without schema_version field defaults to v1 and raises error."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = Path(tmpdir)
        graph_dir = project_root / ".lineagelens"
        graph_dir.mkdir(parents=True)

        # Graph without schema_version field
        old_graph = {
            "project_root": str(project_root),
            "containers": [],
            "symbols": {},
            "relations": [],
        }

        graph_file = graph_dir / "graph.json"
        graph_file.write_text(json.dumps(old_graph))

        # Should detect version mismatch (defaults to 1, expects 3)
        with pytest.raises(GraphNotFoundError) as exc_info:
            load_graph(project_root)

        assert "found v1" in str(exc_info.value)
        assert "expected v3" in str(exc_info.value)


def test_schema_version_immutable():
    """Verify SCHEMA_VERSION constant doesn't accidentally change."""
    # This test ensures the constant stays at 3 unless explicitly changed
    # If someone changes it, this test will fail and force them to review
    # the implications of the schema bump
    assert SCHEMA_VERSION == 3, (
        "SCHEMA_VERSION changed! Review implications: "
        "- All old graphs must be re-analyzed"
        "- Update version history comment in model.py"
        "- Add new test cases for new schema"
    )
