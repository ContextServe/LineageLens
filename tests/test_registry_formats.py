"""Tests for service registry format extraction."""

import tempfile
import unittest
from pathlib import Path

from lineagelens.registry_formats import (
    RegistryFormat,
    find_registry_files,
    parse_registries,
)


class TestJdkServiceLoaderFormat(unittest.TestCase):
    """Test cases for JDK ServiceLoader format."""

    def setUp(self):
        """Set up test format."""
        self.fmt = RegistryFormat(
            name="jdk_service_loader",
            description="JDK ServiceLoader",
            path_pattern=r"META-INF/services/(.+?)(?:\.properties)?$",
            line_format="bare_class_names",
        )

    def test_matches_jdk_registry_path(self):
        """Test that format matches JDK ServiceLoader paths."""
        self.assertTrue(self.fmt.matches_path("META-INF/services/java.lang.String"))
        self.assertTrue(self.fmt.matches_path("META-INF/services/com.example.MyService"))
        self.assertFalse(self.fmt.matches_path("META-INF/services"))
        self.assertFalse(self.fmt.matches_path("src/main/java/MyClass.java"))

    def test_extracts_service_interface_from_path(self):
        """Test extraction of service interface FQN from file path."""
        fqn = self.fmt.extract_interface_fqn("META-INF/services/com.example.Service")
        self.assertEqual(fqn, "com.example.Service")

        fqn = self.fmt.extract_interface_fqn("META-INF/services/java.util.ServiceLoader")
        self.assertEqual(fqn, "java.util.ServiceLoader")

    def test_parses_simple_provider_list(self):
        """Test parsing simple provider class names."""
        content = "com.example.impl.ProviderA\ncom.example.impl.ProviderB"
        result = self.fmt.parse_file("META-INF/services/com.example.Service", content)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["interface_fqn"], "com.example.Service")
        self.assertEqual(result[0]["provider_class"], "com.example.impl.ProviderA")
        self.assertEqual(result[1]["provider_class"], "com.example.impl.ProviderB")

    def test_skips_comment_lines(self):
        """Test that comment lines are skipped."""
        content = "# This is a comment\ncom.example.impl.ProviderA\n# Another comment"
        result = self.fmt.parse_file("META-INF/services/com.example.Service", content)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["provider_class"], "com.example.impl.ProviderA")

    def test_strips_inline_comments(self):
        """Test that inline comments are stripped."""
        content = "com.example.impl.ProviderA # this is inline\ncom.example.impl.ProviderB"
        result = self.fmt.parse_file("META-INF/services/com.example.Service", content)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["provider_class"], "com.example.impl.ProviderA")
        # No comment text should be included

    def test_handles_empty_lines(self):
        """Test that empty lines are skipped."""
        content = "com.example.impl.ProviderA\n\n\ncom.example.impl.ProviderB"
        result = self.fmt.parse_file("META-INF/services/com.example.Service", content)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["provider_class"], "com.example.impl.ProviderA")
        self.assertEqual(result[1]["provider_class"], "com.example.impl.ProviderB")

    def test_handles_whitespace(self):
        """Test that leading/trailing whitespace is handled."""
        content = "  com.example.impl.ProviderA  \n\t com.example.impl.ProviderB \t"
        result = self.fmt.parse_file("META-INF/services/com.example.Service", content)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["provider_class"], "com.example.impl.ProviderA")
        self.assertEqual(result[1]["provider_class"], "com.example.impl.ProviderB")

    def test_realistic_jdk_service_loader_file(self):
        """Test with a realistic JDK ServiceLoader file content."""
        content = """# ServiceLoader for java.nio.charset.spi.CharsetProvider
java.nio.charset.iso8859_1.ISO8859_1
java.nio.charset.sun.NioCharsetProvider
# UTF-8 provider

java.nio.charset.utf_8.UTF_8"""

        result = self.fmt.parse_file("META-INF/services/java.nio.charset.spi.CharsetProvider", content)

        # Should have 3 providers (one is empty line after comment)
        provider_classes = [r["provider_class"] for r in result]
        self.assertIn("java.nio.charset.iso8859_1.ISO8859_1", provider_classes)
        self.assertIn("java.nio.charset.sun.NioCharsetProvider", provider_classes)
        self.assertIn("java.nio.charset.utf_8.UTF_8", provider_classes)


class TestKeyValueFormat(unittest.TestCase):
    """Test cases for key=value format (used by Dubbo)."""

    def setUp(self):
        """Set up test format."""
        self.fmt = RegistryFormat(
            name="dubbo_spi",
            description="Dubbo SPI",
            path_pattern=r"META-INF/dubbo/internal/(.+)$",
            line_format="key_value",
        )

    def test_parses_key_value_format(self):
        """Test parsing key=value format."""
        content = "adaptive=com.alibaba.dubbo.common.extension.AdaptiveExtensionFactory\nstub=com.alibaba.dubbo.rpc.protocol.ProtocolFilterWrapper"
        result = self.fmt.parse_file("META-INF/dubbo/internal/com.alibaba.dubbo.rpc.Protocol", content)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["provider_class"], "com.alibaba.dubbo.common.extension.AdaptiveExtensionFactory")
        self.assertEqual(result[0]["provider_key"], "adaptive")

    def test_skips_lines_without_equals(self):
        """Test that lines without '=' are skipped."""
        content = "valid=com.example.Provider\ninvalidline\nanother=com.example.Provider2"
        result = self.fmt.parse_file("META-INF/dubbo/internal/com.example.Service", content)

        self.assertEqual(len(result), 2)
        provider_classes = [r["provider_class"] for r in result]
        self.assertIn("com.example.Provider", provider_classes)
        self.assertIn("com.example.Provider2", provider_classes)


class TestFindRegistryFiles(unittest.TestCase):
    """Test cases for finding registry files in project."""

    def test_finds_jdk_service_loader_files(self):
        """Test finding JDK ServiceLoader registry files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Create registry file structure
            registry_dir = project_path / "src" / "main" / "resources" / "META-INF" / "services"
            registry_dir.mkdir(parents=True)

            service_file = registry_dir / "com.example.MyService"
            service_file.write_text("com.example.impl.MyProvider")

            files = find_registry_files(project_path)
            rel_paths = [str(f.relative_to(project_path)).replace("\\", "/") for f in files]

            self.assertIn("src/main/resources/META-INF/services/com.example.MyService", rel_paths)

    def test_finds_multiple_registry_files(self):
        """Test finding multiple registry files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            registry_dir = project_path / "src" / "main" / "resources" / "META-INF" / "services"
            registry_dir.mkdir(parents=True)

            # Create multiple service files
            (registry_dir / "java.nio.charset.spi.CharsetProvider").write_text("com.example.ProviderA")
            (registry_dir / "java.util.Iterator").write_text("com.example.ProviderB")

            files = find_registry_files(project_path)
            self.assertGreaterEqual(len(files), 2)


class TestParseRegistries(unittest.TestCase):
    """Test cases for parsing all registries in a project."""

    def test_parse_registries_extracts_all_providers(self):
        """Test that parse_registries extracts all providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            # Create registry file
            registry_dir = project_path / "src" / "main" / "resources" / "META-INF" / "services"
            registry_dir.mkdir(parents=True)

            service_file = registry_dir / "com.example.MyService"
            service_file.write_text("com.example.impl.ProviderA\ncom.example.impl.ProviderB")

            result = parse_registries(project_path)

            self.assertGreaterEqual(len(result), 2)
            provider_classes = [r["provider_class"] for r in result]
            self.assertIn("com.example.impl.ProviderA", provider_classes)
            self.assertIn("com.example.impl.ProviderB", provider_classes)

            # Verify metadata
            for provider in result:
                self.assertIn("interface_fqn", provider)
                self.assertIn("registry_file", provider)
                self.assertIn("registry_format", provider)

    def test_parse_registries_handles_nonexistent_directory(self):
        """Test that parse_registries handles projects without registries gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_path = Path(tmpdir)

            result = parse_registries(project_path)

            # Should return empty list, not error
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
