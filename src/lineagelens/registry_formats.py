"""Service registry format extraction for PROVIDES relations.

Supports multiple service registry formats (SPI, Dubbo, Spring, etc.) through a
data-driven registry format table. Each format defines how to parse a registry file
and extract provider class names.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RegistryFormat:
    """Describes how to extract PROVIDES relations from a registry file format."""

    name: str  # e.g., "jdk_service_loader", "dubbo_spi"
    description: str  # Human-readable description
    path_pattern: str  # Regex pattern to match registry files (group 1 = service interface FQN)
    line_format: str  # How lines are formatted (e.g., "bare_class_names", "key_value")
    weight: int = 10  # Priority when formats overlap (higher = more specific)
    active: bool = True  # Enable/disable this format

    def matches_path(self, file_path: str) -> bool:
        """Check if file path matches this format's pattern."""
        return bool(re.search(self.path_pattern, file_path))

    def extract_interface_fqn(self, file_path: str) -> Optional[str]:
        """Extract the service interface FQN from file path."""
        match = re.search(self.path_pattern, file_path)
        if match:
            return match.group(1)
        return None

    def parse_file(self, file_path: str, content: str) -> list[dict]:
        """Parse registry file and return provider information.

        Returns:
            List of dicts with keys:
            - interface_fqn: Fully qualified service interface name
            - provider_class: Fully qualified or simple provider class name
        """
        if not self.matches_path(file_path):
            return []

        interface_fqn = self.extract_interface_fqn(file_path)
        if not interface_fqn:
            return []

        providers = []

        if self.line_format == "bare_class_names":
            # Each non-comment, non-empty line is a provider class name
            for line in content.split("\n"):
                # Strip comments (text after #)
                if "#" in line:
                    line = line[: line.index("#")]
                line = line.strip()

                # Skip empty lines
                if not line:
                    continue

                providers.append(
                    {
                        "interface_fqn": interface_fqn,
                        "provider_class": line,
                    }
                )

        elif self.line_format == "key_value":
            # Format: key=com.example.ProviderClass
            for line in content.split("\n"):
                # Strip comments
                if "#" in line:
                    line = line[: line.index("#")]
                line = line.strip()

                # Skip empty lines
                if not line or "=" not in line:
                    continue

                # Split on first = only
                key, value = line.split("=", 1)
                if value.strip():
                    providers.append(
                        {
                            "interface_fqn": interface_fqn,
                            "provider_class": value.strip(),
                            "provider_key": key.strip(),  # Optional: preserve key for Dubbo
                        }
                    )

        return providers


# Registry format definitions (data-driven table)
# Each entry describes one format. Add new formats here without modifying code.
REGISTRY_FORMATS = [
    RegistryFormat(
        name="jdk_service_loader",
        description="JDK ServiceLoader (META-INF/services)",
        path_pattern=r"META-INF/services/(.+?)(?:\.properties)?$",
        line_format="bare_class_names",
        weight=10,
        active=True,
    ),
    # Issue #40: Dubbo SPI will go here
    # RegistryFormat(
    #     name="dubbo_spi",
    #     description="Dubbo @SPI (META-INF/dubbo/internal)",
    #     path_pattern=r"META-INF/dubbo/internal/(.+)$",
    #     line_format="key_value",
    #     weight=15,  # More specific than JDK
    #     active=True,
    # ),
]


def find_registry_files(project_path: Path, patterns: Optional[list[str]] = None) -> list[Path]:
    """Find all registry files matching known formats.

    Args:
        project_path: Root of project to scan
        patterns: Optional list of path patterns to search for. If None, all active formats used.

    Returns:
        List of Path objects matching registry format patterns
    """
    if patterns is None:
        # Use all active formats
        patterns = [fmt.path_pattern for fmt in REGISTRY_FORMATS if fmt.active]

    registry_files = []

    # Scan common locations for resource files
    search_roots = [
        project_path / "src" / "main" / "resources",  # Maven/Gradle standard
        project_path / "src" / "resources",
        project_path / "resources",
        project_path,  # Fallback: search entire project
    ]

    for search_root in search_roots:
        if not search_root.exists():
            continue

        # Find all files under search_root that match patterns
        for file_path in search_root.rglob("*"):
            if not file_path.is_file():
                continue

            # Get relative path from project root for matching
            try:
                rel_path = file_path.relative_to(project_path)
            except ValueError:
                rel_path = file_path

            rel_path_str = str(rel_path).replace("\\", "/")  # Normalize for cross-platform

            # Check if file matches any pattern
            # Try matching against the full path and also just the part after META-INF
            for pattern in patterns:
                # Check full path match
                if re.search(pattern, rel_path_str):
                    registry_files.append(file_path)
                    break

    return registry_files


def parse_registries(project_path: Path) -> list[dict]:
    """Parse all registry files in a project.

    Args:
        project_path: Root of project to scan

    Returns:
        List of provider dicts with keys:
        - interface_fqn: Service interface fully qualified name
        - provider_class: Provider class name (may need resolution)
        - registry_file: Path to registry file (relative to project)
        - registry_format: Name of the format that parsed this file
    """
    providers = []

    # Find all potential registry files
    registry_files = find_registry_files(project_path)

    for file_path in registry_files:
        # Get relative path for reporting
        try:
            rel_path = file_path.relative_to(project_path)
        except ValueError:
            rel_path = file_path

        rel_path_str = str(rel_path).replace("\\", "/")

        try:
            content = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Skip files we can't read
            continue

        # Try each registry format
        for registry_format in REGISTRY_FORMATS:
            if not registry_format.active:
                continue

            # Check if file matches this format
            if not registry_format.matches_path(rel_path_str):
                continue

            # Parse file with this format
            parsed_providers = registry_format.parse_file(rel_path_str, content)

            for provider_info in parsed_providers:
                provider_data = {
                    **provider_info,
                    "registry_file": rel_path_str,
                    "registry_format": registry_format.name,
                }
                providers.append(provider_data)

            break  # File matched a format, don't try others

    return providers
