"""Code graph ontology versions and capabilities.

Tracks what relation kinds, evidence tiers, tools, and guarantees are available
in each version of the ontology. Used by agents to understand what a graph can/cannot
express and what they can query.
"""

from __future__ import annotations

# Ontology version definitions
# Each version documents what the code graph can express and what agents can rely on
ONTOLOGY_VERSIONS = {
    "1.0": {
        "description": "Initial ontology with core relation kinds and evidence tiers",
        "released": "2026-01-01",

        # What relation kinds exist in this version
        "relation_kinds": [
            "CALLS",      # Direct invocation (method call, function call)
            "INHERITS",   # Class inheritance (extends) or interface implementation
            "OVERRIDES",  # Method override in subclass
            "DECORATES",  # Decorator/annotation applied
        ],

        # Evidence tiers for confidence levels
        "evidence_tiers": [
            "deterministic_fact",       # From literal syntax (observable directly)
            "deterministic_heuristic",  # From name/signature matching
        ],

        # Resolution status values
        "resolution_values": [
            "resolved",                 # Symbol found directly in graph
            "resolved_via_inference",   # Symbol matched via heuristic
            "external_or_dynamic",      # Analysis hit a limit (reflection, SPI, config)
        ],

        # Available MCP tools
        "tools": [
            "get_symbol",
            "search_symbols",
            "get_callers",              # Returns all relation kinds
            "get_callees",              # Returns all relation kinds
            "get_lineage",
            "get_impact",
            "get_module_overview",
            "list_entry_points",
            "list_risks",
            "get_project_config",
            "get_metrics",
            "find_entry_points",
            "get_module_dependencies",
            "list_dead_code",
            "get_reachability",
            "list_duplicate_names",
            "trigger_analysis",
            "list_fields_by_type",
            "list_implementations",     # Filter get_callers by kind=INHERITS
            "list_providers",           # Filter get_callers by kind=PROVIDES (but PROVIDES not in 1.0)
        ],

        # What agents can rely on
        "guarantees": [
            "CALLS edges represent direct invocations visible in source code",
            "INHERITS edges represent class hierarchy (extends/implements)",
            "All symbols reachable from at least one entry point are classified alive or test_only",
            "Dead code verdicts include reasons (rescue mechanisms) when symbols are rescued",
            "Evidence tier and resolution status provided for every relation",
        ],

        # What agents should NOT assume
        "limitations": [
            "SPI/ServiceLoader dispatch (META-INF/services/*) is NOT visible",
            "Reflection-based dispatch is NOT visible (marked as resolution=external_or_dynamic)",
            "Config-driven routes (Spring, Dubbo config) are NOT visible",
            "Method body internals may be collapsed (single-liner methods not expanded)",
            "PROVIDES relation kind does NOT exist in 1.0 (use list_implementations for INHERITS)",
        ],

        # How to handle gaps
        "handling_external_or_dynamic": [
            "When you see resolution=external_or_dynamic, analysis hit a limit",
            "This means the symbol MAY be called but static analysis cannot prove it",
            "Do NOT conclude 'unreachable' - it may be reachable dynamically",
            "Check for alternative relation chains (e.g., INHERITS for subclasses)",
            "Document findings for manual review or configuration inspection",
        ],
    },
}


def get_ontology_version(version: str) -> dict | None:
    """Get the definition for a specific ontology version.

    Args:
        version: Version string (e.g., "1.0")

    Returns:
        Version definition dict, or None if version unknown
    """
    return ONTOLOGY_VERSIONS.get(version)


def current_ontology_version() -> str:
    """Get the current/latest ontology version.

    Returns:
        Current ontology version string
    """
    return "1.0"
