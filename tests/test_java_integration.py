"""Integration test for Java code graph analysis in LineageLens."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lineagelens.cli import build
from lineagelens.queries import (
    find_duplicate_names,
    get_codebase_metrics,
    get_symbol,
    impact_analysis,
    load_graph,
    search_symbols,
)
from lineagelens.reachability import compute_reachability


class TestJavaIntegration(unittest.TestCase):

    def test_java_project_analysis_integration(self):
        """Test full Java project analysis, graph creation, and query engine compatibility."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Create sample Java project layout
            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "order"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            order_service = java_src_dir / "OrderService.java"
            order_service.write_text(
                """package com.example.order;

import org.springframework.web.bind.annotation.PostMapping;

public class OrderService {

    @PostMapping("/orders")
    public String createOrder(String itemId) {
        processPayment(itemId);
        return "SUCCESS";
    }

    private void processPayment(String itemId) {
        System.out.println("Processing " + itemId);
    }
}
""",
                encoding="utf-8",
            )

            # Run LineageLens build on the Java project
            graph_file, report_file, report = build(tmp_path, quiet=True)

            self.assertTrue(graph_file.exists())
            self.assertEqual(graph_file.name, "graph.json")

            # Verify JSON structure
            raw = json.loads(graph_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 2)
            self.assertGreater(len(raw["symbols"]), 0)

            # Load graph using LineageLens query engine
            graph = load_graph(tmp_path)
            self.assertGreaterEqual(len(graph.symbols), 3)

            # Check symbols
            class_sym = get_symbol(graph, "com.example.order.OrderService")
            self.assertIsNotNone(class_sym)
            self.assertEqual(class_sym.kind, "class")

            method_sym = get_symbol(graph, "com.example.order.OrderService.createOrder")
            self.assertIsNotNone(method_sym)
            self.assertEqual(method_sym.kind, "method")
            self.assertEqual(method_sym.entry_point, "api_route")

            # Check search
            results = search_symbols(graph, "createOrder")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].id, "com.example.order.OrderService.createOrder")

            # Check relations & impact analysis
            impact = impact_analysis(graph, "com.example.order.OrderService.processPayment")
            self.assertEqual(impact.symbol_id, "com.example.order.OrderService.processPayment")
            self.assertGreaterEqual(len(impact.affected), 1)
            self.assertTrue(any("createOrder" in ep for ep in impact.affected_entry_points))

            # Check reachability walk
            reachability = compute_reachability(graph)
            verdict = reachability.explain("com.example.order.OrderService.createOrder")
            self.assertIsNotNone(verdict)
            self.assertEqual(verdict.verdict, "alive")

            metrics = get_codebase_metrics(graph)
            self.assertGreaterEqual(metrics["total_symbols"], 3)
            self.assertGreaterEqual(metrics["total_entry_points"], 1)


if __name__ == "__main__":
    unittest.main()
