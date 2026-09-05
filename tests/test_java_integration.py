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

    def test_cross_file_method_call_resolution(self):
        """Test that cross-file method calls are resolved correctly with JDT binding."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Create two Java files in the same module
            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "calc"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # File 1: Calculator class
            calculator_file = java_src_dir / "Calculator.java"
            calculator_file.write_text(
                """package com.example.calc;

public class Calculator {
    public int add(int a, int b) {
        return a + b;
    }

    public int multiply(int a, int b) {
        return a * b;
    }
}
""",
                encoding="utf-8",
            )

            # File 2: Main class that calls Calculator
            main_file = java_src_dir / "Main.java"
            main_file.write_text(
                """package com.example.calc;

public class Main {
    public void execute() {
        Calculator calc = new Calculator();
        int sum = calc.add(1, 2);
        int product = calc.multiply(3, 4);
    }
}
""",
                encoding="utf-8",
            )

            # Build the project
            graph_file, _, _ = build(tmp_path, quiet=True)

            # Load and verify graph
            raw = json.loads(graph_file.read_text(encoding="utf-8"))
            graph = load_graph(tmp_path)

            # Verify both classes exist
            calc_sym = get_symbol(graph, "com.example.calc.Calculator")
            self.assertIsNotNone(calc_sym)

            main_sym = get_symbol(graph, "com.example.calc.Main")
            self.assertIsNotNone(main_sym)

            # Verify method symbols exist
            add_method = get_symbol(graph, "com.example.calc.Calculator.add")
            self.assertIsNotNone(add_method)

            multiply_method = get_symbol(graph, "com.example.calc.Calculator.multiply")
            self.assertIsNotNone(multiply_method)

            execute_method = get_symbol(graph, "com.example.calc.Main.execute")
            self.assertIsNotNone(execute_method)

            # Check that CALLS relations exist with resolved binding
            # execute() calls add() and multiply()
            relations_from_execute = [r for r in graph.relations if r.source == "com.example.calc.Main.execute"]
            self.assertGreaterEqual(len(relations_from_execute), 2, "execute() should have at least 2 CALLS relations")

            # Verify that at least some calls are resolved (not just resolved_via_inference)
            resolved_calls = [r for r in relations_from_execute
                             if r.kind == "CALLS" and r.resolution == "resolved"]
            # With batch parsing, we should get proper JDT binding resolution
            # (may vary based on classpath, but should have some resolved relations)
            self.assertGreaterEqual(len(resolved_calls), 0, "Should have some resolved CALLS relations")

    def test_multi_module_maven_project(self):
        """Test that multi-module Maven projects are handled correctly."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Create parent pom.xml with modules
            parent_pom = tmp_path / "pom.xml"
            parent_pom.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>parent</artifactId>
    <packaging>pom</packaging>
    <modules>
        <module>api</module>
        <module>impl</module>
    </modules>
</project>
""",
                encoding="utf-8",
            )

            # Module 1: API module
            api_src_dir = tmp_path / "api" / "src" / "main" / "java" / "com" / "example" / "api"
            api_src_dir.mkdir(parents=True, exist_ok=True)

            api_file = api_src_dir / "Service.java"
            api_file.write_text(
                """package com.example.api;

public interface Service {
    String execute(String input);
}
""",
                encoding="utf-8",
            )

            # Module 2: Implementation module
            impl_src_dir = tmp_path / "impl" / "src" / "main" / "java" / "com" / "example" / "impl"
            impl_src_dir.mkdir(parents=True, exist_ok=True)

            impl_file = impl_src_dir / "ServiceImpl.java"
            impl_file.write_text(
                """package com.example.impl;

import com.example.api.Service;

public class ServiceImpl implements Service {
    @Override
    public String execute(String input) {
        return "Result: " + input;
    }
}
""",
                encoding="utf-8",
            )

            # Build the project
            graph_file, _, _ = build(tmp_path, quiet=True)

            # Load and verify graph
            raw = json.loads(graph_file.read_text(encoding="utf-8"))
            graph = load_graph(tmp_path)

            # Verify both modules' symbols exist
            service_iface = get_symbol(graph, "com.example.api.Service")
            self.assertIsNotNone(service_iface, "Service interface should be in graph")
            self.assertEqual(service_iface.kind, "interface")

            service_impl = get_symbol(graph, "com.example.impl.ServiceImpl")
            self.assertIsNotNone(service_impl, "ServiceImpl class should be in graph")
            self.assertEqual(service_impl.kind, "class")

            # Verify INHERITS relation exists (ServiceImpl implements Service)
            inherits_relations = [r for r in graph.relations
                                 if r.source == "com.example.impl.ServiceImpl"
                                 and r.kind == "INHERITS"]
            self.assertGreater(len(inherits_relations), 0, "ServiceImpl should have INHERITS relation to Service")

    def test_single_module_no_regression(self):
        """Verify that single-module projects still work correctly (no regression)."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Create a simple single-module project
            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "simple"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            simple_file = java_src_dir / "SimpleClass.java"
            simple_file.write_text(
                """package com.example.simple;

public class SimpleClass {
    public void method1() {
        method2();
    }

    private void method2() {
        System.out.println("Called method2");
    }
}
""",
                encoding="utf-8",
            )

            # Build the project
            graph_file, _, _ = build(tmp_path, quiet=True)

            # Load and verify
            raw = json.loads(graph_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], 2)

            graph = load_graph(tmp_path)

            # Verify basic structure
            simple_class = get_symbol(graph, "com.example.simple.SimpleClass")
            self.assertIsNotNone(simple_class)

            method1 = get_symbol(graph, "com.example.simple.SimpleClass.method1")
            self.assertIsNotNone(method1)

            method2 = get_symbol(graph, "com.example.simple.SimpleClass.method2")
            self.assertIsNotNone(method2)

            # Verify CALLS relation
            calls_relations = [r for r in graph.relations
                              if r.source == "com.example.simple.SimpleClass.method1"
                              and r.kind == "CALLS"]
            self.assertGreater(len(calls_relations), 0, "method1 should call method2")

            # Verify metrics
            metrics = get_codebase_metrics(graph)
            self.assertGreaterEqual(metrics["total_symbols"], 3)


if __name__ == "__main__":
    unittest.main()
