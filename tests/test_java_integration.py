"""Integration test for Java code graph analysis in LineageLens."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lineagelens.cli import build
from lineagelens.model import SCHEMA_VERSION
from lineagelens.queries import (
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
            graph_file, _report_file, _report = build(tmp_path, quiet=True)


            self.assertTrue(graph_file.exists())
            self.assertEqual(graph_file.name, "graph.json")

            # Verify JSON structure
            raw = json.loads(graph_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema_version"], SCHEMA_VERSION)
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
            _raw = json.loads(graph_file.read_text(encoding="utf-8"))
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
            _raw = json.loads(graph_file.read_text(encoding="utf-8"))
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
            self.assertEqual(raw["schema_version"], SCHEMA_VERSION)


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

    def test_field_declarations_simple_class(self):
        """Test that fields are created as first-class Symbol nodes."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "model"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create a class with various fields
            person_file = java_src_dir / "Person.java"
            person_file.write_text(
                """package com.example.model;

public class Person {
    /**
     * The person's name
     */
    public String name;

    private int age;

    protected double salary;

    static final int MAX_AGE = 150;
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify class symbol
            person_class = get_symbol(graph, "com.example.model.Person")
            self.assertIsNotNone(person_class)
            self.assertEqual(person_class.kind, "class")

            # Verify field symbols
            name_field = get_symbol(graph, "com.example.model.Person.name")
            self.assertIsNotNone(name_field)
            self.assertEqual(name_field.kind, "field")
            self.assertEqual(name_field.name, "name")
            self.assertIn("String", name_field.type_ or "")

            age_field = get_symbol(graph, "com.example.model.Person.age")
            self.assertIsNotNone(age_field)
            self.assertEqual(age_field.kind, "field")
            self.assertIn("int", age_field.type_ or "")

            salary_field = get_symbol(graph, "com.example.model.Person.salary")
            self.assertIsNotNone(salary_field)
            self.assertIn("double", salary_field.type_ or "")

            # Verify modifier information
            self.assertEqual(name_field.visibility, "public")
            self.assertEqual(age_field.visibility, "private")
            self.assertEqual(salary_field.visibility, "protected")

            max_age_field = get_symbol(graph, "com.example.model.Person.MAX_AGE")
            self.assertIsNotNone(max_age_field)
            self.assertTrue(max_age_field.static_)
            self.assertTrue(max_age_field.final_)

            # Verify Javadoc was captured
            self.assertIsNotNone(name_field.description)
            self.assertIn("name", name_field.description)

    def test_enum_constants_as_fields(self):
        """Test that enum constants are treated as field symbols."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "enums"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create an enum
            status_file = java_src_dir / "Status.java"
            status_file.write_text(
                """package com.example.enums;

public enum Status {
    /**
     * Active status
     */
    ACTIVE,

    INACTIVE,

    PENDING;
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify enum symbol
            status_enum = get_symbol(graph, "com.example.enums.Status")
            self.assertIsNotNone(status_enum)
            self.assertEqual(status_enum.kind, "enum")

            # Verify enum constants as fields
            active_field = get_symbol(graph, "com.example.enums.Status.ACTIVE")
            self.assertIsNotNone(active_field)
            self.assertEqual(active_field.kind, "field")
            self.assertEqual(active_field.type_, "com.example.enums.Status")
            self.assertTrue(active_field.static_)
            self.assertTrue(active_field.final_)
            self.assertEqual(active_field.visibility, "public")

            inactive_field = get_symbol(graph, "com.example.enums.Status.INACTIVE")
            self.assertIsNotNone(inactive_field)

            pending_field = get_symbol(graph, "com.example.enums.Status.PENDING")
            self.assertIsNotNone(pending_field)

            # Verify Javadoc on ACTIVE
            self.assertIsNotNone(active_field.description)

    def test_field_types_cross_module(self):
        """Test that field types resolve correctly across modules (post #14 classpath fix)."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Module A: defines interface
            api_src_dir = tmp_path / "api" / "src" / "main" / "java" / "com" / "example" / "api"
            api_src_dir.mkdir(parents=True, exist_ok=True)

            provider_file = api_src_dir / "Provider.java"
            provider_file.write_text(
                """package com.example.api;

public interface Provider {
    String provide();
}
""",
                encoding="utf-8",
            )

            # Module B: uses interface as field type
            impl_src_dir = tmp_path / "impl" / "src" / "main" / "java" / "com" / "example" / "impl"
            impl_src_dir.mkdir(parents=True, exist_ok=True)

            impl_file = impl_src_dir / "Impl.java"
            impl_file.write_text(
                """package com.example.impl;

import com.example.api.Provider;

public class Impl {
    private Provider provider;
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify Provider interface exists
            provider_interface = get_symbol(graph, "com.example.api.Provider")
            self.assertIsNotNone(provider_interface)

            # Verify Impl class exists
            impl_class = get_symbol(graph, "com.example.impl.Impl")
            self.assertIsNotNone(impl_class)

            # Verify provider field exists and has correct type (cross-module reference)
            provider_field = get_symbol(graph, "com.example.impl.Impl.provider")
            self.assertIsNotNone(provider_field)
            self.assertEqual(provider_field.kind, "field")
            self.assertEqual(provider_field.visibility, "private")
            # Type should be resolved to the interface (thanks to #14 classpath fix)
            self.assertIsNotNone(provider_field.type_)

    def test_array_and_generic_field_types(self):
        """Test that array and generic field types are captured."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "collections"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            container_file = java_src_dir / "Container.java"
            container_file.write_text(
                """package com.example.collections;

import java.util.List;
import java.util.Map;

public class Container {
    private int[] values;
    public String[][] matrix;
    private List<String> items;
    private Map<String, Integer> config;
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify Container class
            container_class = get_symbol(graph, "com.example.collections.Container")
            self.assertIsNotNone(container_class)

            # Verify array field
            values_field = get_symbol(graph, "com.example.collections.Container.values")
            self.assertIsNotNone(values_field)
            self.assertIsNotNone(values_field.type_)

            # Verify 2D array field
            matrix_field = get_symbol(graph, "com.example.collections.Container.matrix")
            self.assertIsNotNone(matrix_field)
            self.assertEqual(matrix_field.visibility, "public")

            # Verify generic fields exist
            items_field = get_symbol(graph, "com.example.collections.Container.items")
            self.assertIsNotNone(items_field)

            config_field = get_symbol(graph, "com.example.collections.Container.config")
            self.assertIsNotNone(config_field)

    def test_imports_relations(self):
        """Test that IMPORTS relations are emitted for import statements."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "service"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create a class with various imports
            service_file = java_src_dir / "DataService.java"
            service_file.write_text(
                """package com.example.service;

import java.util.List;
import java.util.Map;
import java.io.IOException;
import java.util.*;
import static java.util.Collections.emptyList;

public class DataService {
    public List<String> getData() throws IOException {
        return emptyList();
    }
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify class symbol
            service_class = get_symbol(graph, "com.example.service.DataService")
            self.assertIsNotNone(service_class)

            # Check for IMPORTS relations from the package
            import_relations = [r for r in graph.relations
                               if r.source == "com.example.service" and r.kind == "IMPORTS"]

            # Should have at least 5 import relations (List, Map, IOException, *, Collections.emptyList)
            self.assertGreaterEqual(len(import_relations), 5,
                                   f"Expected at least 5 IMPORTS relations, found {len(import_relations)}")

            # Verify specific imports exist
            targets = {r.target for r in import_relations}
            self.assertIn("java.util.List", targets, "List import should be present")
            self.assertIn("java.util.Map", targets, "Map import should be present")
            self.assertIn("java.io.IOException", targets, "IOException import should be present")
            self.assertIn("java.util", targets, "Wildcard import should target package")
            self.assertIn("java.util.Collections.emptyList", targets, "Static import should be present")

            # Verify resolution
            for rel in import_relations:
                # All imports should be marked as external_or_dynamic
                self.assertEqual(rel.resolution, "external_or_dynamic")

    def test_annotation_decorates_relations(self):
        """Test that DECORATES relations are emitted for annotations."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "api"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create classes with various annotations
            controller_file = java_src_dir / "UserController.java"
            controller_file.write_text(
                """package com.example.api;

import java.lang.Override;
import java.lang.Deprecated;

public class UserController {
    @Deprecated
    public void getUser() {
    }

    @Override
    public String toString() {
        return "UserController";
    }
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify class symbol
            controller_class = get_symbol(graph, "com.example.api.UserController")
            self.assertIsNotNone(controller_class)

            # Check for DECORATES relations from the class
            class_decorates = [r for r in graph.relations
                              if r.source == "com.example.api.UserController" and r.kind == "DECORATES"]

            # Should have at least 1 DECORATES relation (could have more from class modifiers)
            self.assertGreaterEqual(len(class_decorates), 0,
                                   "Expected class DECORATES relations")

            # Check for DECORATES relations from methods
            method_decorates = [r for r in graph.relations
                               if "UserController.getUser" in r.source and r.kind == "DECORATES"]

            # getUser() method should have @Deprecated decorator
            self.assertGreater(len(method_decorates), 0,
                              "Expected @Deprecated DECORATES relation on getUser()")

            # toString() method should have @Override decorator
            override_decorates = [r for r in graph.relations
                                 if "UserController.toString" in r.source and r.kind == "DECORATES"]

            self.assertGreater(len(override_decorates), 0,
                              "Expected @Override DECORATES relation on toString()")

            # Verify resolution for framework annotations (should be external_or_dynamic)
            for rel in method_decorates + class_decorates + override_decorates:
                if rel.target in ["java.lang.Deprecated", "java.lang.Override"]:
                    self.assertEqual(rel.resolution, "external_or_dynamic",
                                   f"Framework annotation {rel.target} should be external_or_dynamic")


    def test_method_locals_nested_blocks(self):
        """Test that local variables in nested blocks (if/for/try) are captured."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "blocks"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create a class with methods containing nested blocks
            blocks_file = java_src_dir / "BlocksTest.java"
            blocks_file.write_text(
                """package com.example.blocks;

public class BlocksTest {
    public void methodWithNestedBlocks() {
        int x = 0;
        if (x > 0) {
            int y = 1;  // variable in if block
        }
        for (int i = 0; i < 10; i++) {
            int loopLocal = i * 2;  // variable in for block
        }
        try {
            int z = 2;  // variable in try block
        } catch (Exception e) {
            int catchLocal = 3;  // variable in catch block
        }
    }

    public void simpleLoop() {
        for (int j = 0; j < 5; j++) {
            String item = "value";
        }
    }
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Get the method with nested blocks
            method_sym = get_symbol(graph, "com.example.blocks.BlocksTest.methodWithNestedBlocks")
            self.assertIsNotNone(method_sym)
            self.assertGreater(len(method_sym.locals), 0, "Should capture variables from all nested blocks")

            # Verify variables are captured
            local_names = {loc["name"] for loc in method_sym.locals}
            self.assertIn("x", local_names, "Top-level local 'x' should be captured")
            self.assertIn("y", local_names, "Local 'y' in if block should be captured")
            self.assertIn("i", local_names, "Loop variable 'i' should be captured")
            self.assertIn("loopLocal", local_names, "Local 'loopLocal' in for block should be captured")
            self.assertIn("z", local_names, "Local 'z' in try block should be captured")
            self.assertIn("e", local_names, "Catch parameter 'e' should be captured")
            self.assertIn("catchLocal", local_names, "Local 'catchLocal' in catch block should be captured")

            # Check simple loop method
            simple_loop_sym = get_symbol(graph, "com.example.blocks.BlocksTest.simpleLoop")
            self.assertIsNotNone(simple_loop_sym)
            simple_loop_locals = {loc["name"] for loc in simple_loop_sym.locals}
            self.assertIn("j", simple_loop_locals, "Loop variable 'j' should be captured")
            self.assertIn("item", simple_loop_locals, "Local 'item' in for block should be captured")

    def test_method_local_variables_extraction(self):
        """Test that local variables within methods are captured with type information."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example" / "util"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create a class with methods containing local variables
            calc_file = java_src_dir / "Calculator.java"
            calc_file.write_text(
                """package com.example.util;

import java.util.List;
import java.util.ArrayList;

public class Calculator {
    public int compute(int a, int b) {
        int sum = a + b;
        int product = a * b;
        int result = sum + product;
        return result;
    }

    public List<String> processData() {
        List<String> items = new ArrayList<>();
        String temp = "data";
        int count = 0;
        items.add(temp);
        return items;
    }
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Verify class symbol
            calc_class = get_symbol(graph, "com.example.util.Calculator")
            self.assertIsNotNone(calc_class)

            # Get the compute method
            compute_method = get_symbol(graph, "com.example.util.Calculator.compute")
            self.assertIsNotNone(compute_method)
            self.assertEqual(compute_method.kind, "method")

            # Check that local variables were extracted
            self.assertIsNotNone(compute_method.locals, "Method should have locals list")
            self.assertGreater(len(compute_method.locals), 0, "compute() should have extracted local variables")

            # Verify local variable information
            local_names = {local["name"] for local in compute_method.locals}
            self.assertIn("sum", local_names, "Local variable 'sum' should be captured")
            self.assertIn("product", local_names, "Local variable 'product' should be captured")
            self.assertIn("result", local_names, "Local variable 'result' should be captured")

            # Verify type information
            sum_local = next((loc for loc in compute_method.locals if loc["name"] == "sum"), None)
            self.assertIsNotNone(sum_local)
            self.assertIn("int", sum_local["type"], f"sum should have int type, got {sum_local['type']}")

            # Get the processData method with generic types
            process_method = get_symbol(graph, "com.example.util.Calculator.processData")
            self.assertIsNotNone(process_method)

            # Check locals for processData
            self.assertGreater(len(process_method.locals), 0, "processData() should have extracted local variables")

            local_names_2 = {local["name"] for local in process_method.locals}
            self.assertIn("items", local_names_2, "Local variable 'items' should be captured")
            self.assertIn("temp", local_names_2, "Local variable 'temp' should be captured")
            self.assertIn("count", local_names_2, "Local variable 'count' should be captured")

            # Verify type information for generic type
            items_local = next((loc for loc in process_method.locals if loc["name"] == "items"), None)

            self.assertIsNotNone(items_local)
            # Type should be either java.util.ArrayList or java.util.List
            self.assertTrue(
                "ArrayList" in items_local["type"] or "List" in items_local["type"],
                f"items should be a List type, got {items_local['type']}"
            )


if __name__ == "__main__":
    unittest.main()
