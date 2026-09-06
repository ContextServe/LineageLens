"""Tests for MCP server ontology instructions."""

import unittest

from lineagelens.mcp_server import _get_ontology_instructions


class TestOntologyInstructions(unittest.TestCase):
    """Test cases for MCP ontology instructions."""

    def test_ontology_instructions_non_empty(self):
        """Test that ontology instructions are non-empty."""
        instructions = _get_ontology_instructions()
        self.assertIsInstance(instructions, str)
        self.assertGreater(len(instructions), 100)  # Substantive content

    def test_ontology_includes_relation_kinds(self):
        """Test that ontology documents all relation kinds."""
        instructions = _get_ontology_instructions()
        relation_kinds = ["CALLS", "INHERITS", "OVERRIDES", "DECORATES", "PROVIDES"]
        for kind in relation_kinds:
            with self.subTest(kind=kind):
                self.assertIn(kind, instructions)

    def test_ontology_includes_evidence_tiers(self):
        """Test that ontology documents evidence tiers."""
        instructions = _get_ontology_instructions()
        evidence_tiers = ["deterministic_fact", "deterministic_heuristic"]
        for tier in evidence_tiers:
            with self.subTest(tier=tier):
                self.assertIn(tier, instructions)

    def test_ontology_includes_resolution_values(self):
        """Test that ontology documents resolution values."""
        instructions = _get_ontology_instructions()
        resolution_values = ["resolved", "resolved_via_inference", "external_or_dynamic"]
        for value in resolution_values:
            with self.subTest(value=value):
                self.assertIn(value, instructions)

    def test_ontology_includes_query_routing(self):
        """Test that ontology includes query routing guidance."""
        instructions = _get_ontology_instructions()
        routing_keywords = ["Query Routing", "get_callers", "get_lineage", "list_providers"]
        for keyword in routing_keywords:
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, instructions)

    def test_ontology_includes_agent_rules(self):
        """Test that ontology includes agent decision rules."""
        instructions = _get_ontology_instructions()
        rule_keywords = ["Agent Decision Rules", "external_or_dynamic", "verification"]
        for keyword in rule_keywords:
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, instructions)

    def test_ontology_includes_limitations(self):
        """Test that ontology documents known limitations."""
        instructions = _get_ontology_instructions()
        limitations = [
            "SPI",
            "Reflection",
            "Config-driven",
            "Method Body",
        ]
        for limitation in limitations:
            with self.subTest(limitation=limitation):
                self.assertIn(limitation, instructions)

    def test_ontology_includes_version_info(self):
        """Test that ontology documents version information."""
        instructions = _get_ontology_instructions()
        self.assertIn("Ontology Version", instructions)
        self.assertIn("schema version", instructions)

    def test_ontology_has_sections(self):
        """Test that ontology is well-structured with sections."""
        instructions = _get_ontology_instructions()
        sections = [
            "Relation Kinds",
            "Evidence Tiers",
            "Query Routing",
            "Known Limitations",
            "Agent Decision Rules",
        ]
        for section in sections:
            with self.subTest(section=section):
                self.assertIn(section, instructions)

    def test_ontology_mentions_spi_limitation(self):
        """Test that ontology specifically mentions SPI as a limitation."""
        instructions = _get_ontology_instructions()
        self.assertIn("META-INF/services", instructions)
        self.assertIn("META-INF/dubbo/internal", instructions)
        self.assertIn("Issue #39", instructions)

    def test_ontology_mentions_reflection(self):
        """Test that ontology mentions reflection-based dispatch."""
        instructions = _get_ontology_instructions()
        self.assertIn("Reflection", instructions)
        self.assertIn("getMethod", instructions)

    def test_ontology_provides_tool_filtering_guidance(self):
        """Test that ontology explains how to filter relations by kind."""
        instructions = _get_ontology_instructions()
        self.assertIn("Filter by", instructions)
        self.assertIn("relation.kind", instructions)


if __name__ == "__main__":
    unittest.main()
