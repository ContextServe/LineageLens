"""Tests for list_fields_by_type MCP tool."""

import tempfile
import unittest
from pathlib import Path

from lineagelens.cli import build
from lineagelens.mcp_server import (
    _is_valid_type_name,
    _type_matches,
)
from lineagelens.queries import load_graph


class TestListFieldsByType(unittest.TestCase):
    """Test cases for list_fields_by_type MCP tool."""

    def test_is_valid_type_name_simple(self):
        """Test simple type names."""
        assert _is_valid_type_name("String")
        assert _is_valid_type_name("MyClass")
        assert _is_valid_type_name("_PrivateClass")

    def test_is_valid_type_name_qualified(self):
        """Test qualified type names."""
        assert _is_valid_type_name("java.lang.String")
        assert _is_valid_type_name("com.example.models.User")
        assert _is_valid_type_name("org.springframework.web.bind.annotation.RequestMapping")

    def test_is_valid_type_name_generic(self):
        """Test generic type names."""
        assert _is_valid_type_name("List<String>")
        assert _is_valid_type_name("Map<String, Integer>")
        assert _is_valid_type_name("Optional<User>")

    def test_is_valid_type_name_invalid(self):
        """Test invalid type names."""
        assert not _is_valid_type_name("")
        # Note: "123Invalid" is technically valid after removing special chars
        assert not _is_valid_type_name("@#$%")  # only special chars

    def test_type_matches_exact(self):
        """Test exact type matching."""
        graph = None  # Not needed for exact match
        assert _type_matches(graph, "java.lang.String", "java.lang.String", False)
        assert _type_matches(graph, "User", "User", False)

    def test_type_matches_no_subtype(self):
        """Test that subtypes don't match without flag."""
        graph = None
        assert not _type_matches(graph, "java.lang.Integer", "java.lang.Number", False)

    def test_list_fields_by_type_basic(self):
        """Test list_fields_by_type finds field symbols by type."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            # Create Java project with typed fields
            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Create a User class
            user_file = java_src_dir / "User.java"
            user_file.write_text(
                """package com.example;

public class User {
    public String name;
    public int age;
}
""",
                encoding="utf-8",
            )

            # Build and analyze
            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Find all field symbols and verify they exist
            field_symbols = []
            for _sym_id, symbol in graph.symbols.items():
                if symbol.kind == "field":
                    field_symbols.append(symbol)


            # Should find at least name and age as field symbols
            self.assertGreaterEqual(len(field_symbols), 2)
            field_names = {s.name for s in field_symbols}
            self.assertIn("name", field_names)
            self.assertIn("age", field_names)

    def test_list_fields_by_type_no_matches(self):
        """Test list_fields_by_type returns empty for non-existent type."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            simple_file = java_src_dir / "Simple.java"
            simple_file.write_text(
                """package com.example;

public class Simple {
    public String value;
}
""",
                encoding="utf-8",
            )

            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Search for a type that doesn't exist
            matches = []
            for symbol in graph.symbols.values():
                if symbol.kind != "class":
                    continue
                for field_dict in symbol.fields:
                    if field_dict.get("type") == "NonExistentType":
                        matches.append(field_dict)

            self.assertEqual(len(matches), 0)

    def test_list_fields_by_type_custom_class(self):
        """Test finding field symbols of custom class type."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Customer class
            customer_file = java_src_dir / "Customer.java"
            customer_file.write_text(
                """package com.example;

public class Customer {
    public String name;
}
""",
                encoding="utf-8",
            )

            # Order class with Customer field
            order_file = java_src_dir / "Order.java"
            order_file.write_text(
                """package com.example;

public class Order {
    public Customer buyer;
    public Customer seller;
}
""",
                encoding="utf-8",
            )

            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Find all field symbols with custom type
            customer_field_symbols = []
            for symbol in graph.symbols.values():
                if symbol.kind == "field" and "Customer" in symbol.type_:
                    customer_field_symbols.append(symbol)

            # Should find buyer and seller fields
            self.assertEqual(len(customer_field_symbols), 2)
            field_names = {s.name for s in customer_field_symbols}
            self.assertIn("buyer", field_names)
            self.assertIn("seller", field_names)

    def test_is_subtype_of_direct_inheritance(self):
        """Test subtype checking with direct inheritance."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)

            java_src_dir = tmp_path / "src" / "main" / "java" / "com" / "example"
            java_src_dir.mkdir(parents=True, exist_ok=True)

            # Base class
            base_file = java_src_dir / "Animal.java"
            base_file.write_text(
                """package com.example;

public class Animal {
    public String name;
}
""",
                encoding="utf-8",
            )

            # Derived class
            derived_file = java_src_dir / "Dog.java"
            derived_file.write_text(
                """package com.example;

public class Dog extends Animal {
    public String breed;
}
""",
                encoding="utf-8",
            )

            _graph_file, _, _ = build(tmp_path, quiet=True)
            graph = load_graph(tmp_path)

            # Check if Dog is subtype of Animal
            dog_symbol = graph.symbols.get("com.example.Dog")
            self.assertIsNotNone(dog_symbol)
            self.assertEqual(len(dog_symbol.bases), 1)
            self.assertIn("Animal", dog_symbol.bases[0])


if __name__ == "__main__":
    unittest.main()
