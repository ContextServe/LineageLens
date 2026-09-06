"""Tests for ontology versioning."""

import json
import tempfile
import unittest
from pathlib import Path

from lineagelens.model import CodeGraph
from lineagelens.ontology import (
    ONTOLOGY_VERSIONS,
    current_ontology_version,
    get_ontology_version,
)


class TestOntologyVersioning(unittest.TestCase):
    """Test cases for ontology versioning."""

    def test_codegraph_has_ontology_version(self):
        """Test that CodeGraph has ontology_version field."""
        graph = CodeGraph(".")
        self.assertEqual(graph.ontology_version, "1.0")

    def test_ontology_version_default_is_1_0(self):
        """Test that default ontology version is 1.0."""
        graph = CodeGraph("/project/root")
        self.assertEqual(graph.ontology_version, "1.0")

    def test_ontology_version_can_be_set(self):
        """Test that ontology_version can be set."""
        graph = CodeGraph(".")
        graph.ontology_version = "1.1"
        self.assertEqual(graph.ontology_version, "1.1")

    def test_ontology_version_in_to_dict(self):
        """Test that ontology_version is included in to_dict()."""
        graph = CodeGraph(".")
        graph.ontology_version = "1.0"
        data = graph.to_dict()

        self.assertIn("ontology_version", data)
        self.assertEqual(data["ontology_version"], "1.0")

    def test_ontology_version_persists_in_json(self):
        """Test that ontology_version survives JSON serialization."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create and serialize graph
            graph = CodeGraph(str(tmp_path))
            graph.ontology_version = "1.0"

            graph_file = tmp_path / "graph.json"
            data = graph.to_dict()
            graph_file.write_text(json.dumps(data))

            # Read back and verify
            loaded_data = json.loads(graph_file.read_text())
            self.assertEqual(loaded_data["ontology_version"], "1.0")


class TestOntologyVersionDefinitions(unittest.TestCase):
    """Test that ontology version definitions are complete."""

    def test_version_1_0_exists(self):
        """Test that version 1.0 is defined."""
        self.assertIn("1.0", ONTOLOGY_VERSIONS)

    def test_version_has_description(self):
        """Test that version has description."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("description", version)
        self.assertIsInstance(version["description"], str)

    def test_version_1_0_relation_kinds(self):
        """Test that version 1.0 defines core relation kinds."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("relation_kinds", version)

        kinds = version["relation_kinds"]
        self.assertIn("CALLS", kinds)
        self.assertIn("INHERITS", kinds)
        self.assertIn("OVERRIDES", kinds)
        self.assertIn("DECORATES", kinds)

        # PROVIDES should NOT be in 1.0
        self.assertNotIn("PROVIDES", kinds)

    def test_version_1_0_evidence_tiers(self):
        """Test that version 1.0 defines evidence tiers."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("evidence_tiers", version)

        tiers = version["evidence_tiers"]
        self.assertIn("deterministic_fact", tiers)
        self.assertIn("deterministic_heuristic", tiers)

    def test_version_1_0_resolution_values(self):
        """Test that version 1.0 defines resolution values."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("resolution_values", version)

        values = version["resolution_values"]
        self.assertIn("resolved", values)
        self.assertIn("resolved_via_inference", values)
        self.assertIn("external_or_dynamic", values)

    def test_version_1_0_tools(self):
        """Test that version 1.0 lists available tools."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("tools", version)

        tools = version["tools"]
        self.assertIn("get_symbol", tools)
        self.assertIn("get_callers", tools)
        self.assertIn("list_implementations", tools)
        self.assertIn("list_providers", tools)

    def test_version_1_0_guarantees(self):
        """Test that version 1.0 documents guarantees."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("guarantees", version)

        guarantees = version["guarantees"]
        self.assertIsInstance(guarantees, list)
        self.assertGreater(len(guarantees), 0)

    def test_version_1_0_limitations(self):
        """Test that version 1.0 documents limitations."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("limitations", version)

        limitations = version["limitations"]
        self.assertIsInstance(limitations, list)
        self.assertGreater(len(limitations), 0)

        # Should mention SPI/ServiceLoader
        limitations_text = " ".join(limitations)
        self.assertIn("SPI", limitations_text.upper())

    def test_version_1_0_handling_external_or_dynamic(self):
        """Test that version 1.0 documents how to handle external_or_dynamic."""
        version = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("handling_external_or_dynamic", version)

        guidance = version["handling_external_or_dynamic"]
        self.assertIsInstance(guidance, list)
        self.assertGreater(len(guidance), 0)


class TestOntologyVersionFunctions(unittest.TestCase):
    """Test ontology version query functions."""

    def test_get_ontology_version_1_0(self):
        """Test retrieving version 1.0 definition."""
        version = get_ontology_version("1.0")
        self.assertIsNotNone(version)
        self.assertIn("relation_kinds", version)

    def test_get_ontology_version_unknown(self):
        """Test retrieving unknown version."""
        version = get_ontology_version("99.99")
        self.assertIsNone(version)

    def test_current_ontology_version(self):
        """Test that current version is retrievable."""
        current = current_ontology_version()
        self.assertEqual(current, "1.0")
        self.assertIn(current, ONTOLOGY_VERSIONS)


if __name__ == "__main__":
    unittest.main()
