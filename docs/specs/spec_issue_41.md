# Spec: Issue #41 - Add Ontology Versioning to CodeGraph

## Overview
Add an `ontology_version` field to the CodeGraph schema to track which version of the code ontology (relation kinds, evidence tiers, tools, guarantees) the graph was built under. This enables tracking schema evolution and helps agents understand what they can rely on.

## Problem Statement
Currently:
- CodeGraph has `schema_version` (tracks JSON schema format changes)
- But no `ontology_version` (tracks semantic meaning/capabilities)
- As new relation kinds are added (e.g., PROVIDES in future), tools won't know which version they're working with
- Agents can't distinguish "old graph without PROVIDES" from "new graph with PROVIDES"

## Solution Design

### 1. Add `ontology_version` Field to CodeGraph
**Location**: `src/lineagelens/model.py`, class `CodeGraph`

```python
@dataclass
class CodeGraph:
    """Code graph data model."""
    
    project_root: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    containers: dict[str, Container] = field(default_factory=dict)
    resiliency_signals: list[ResiliencySignal] = field(default_factory=list)
    schema_version: int = 3
    
    # NEW: Track ontology version
    ontology_version: str = "1.0"  # e.g., "1.0", "1.1", "2.0"
```

### 2. Define Ontology Versions
Create a table documenting each ontology version's capabilities:

**Location**: `src/lineagelens/ontology.py` (new file)

```python
ONTOLOGY_VERSIONS = {
    "1.0": {
        "description": "Initial ontology with CALLS, INHERITS, OVERRIDES, DECORATES",
        "relation_kinds": ["CALLS", "INHERITS", "OVERRIDES", "DECORATES"],
        "evidence_tiers": ["deterministic_fact", "deterministic_heuristic"],
        "resolution_values": ["resolved", "resolved_via_inference", "external_or_dynamic"],
        "tools": [
            "get_symbol",
            "search_symbols", 
            "get_callers",
            "get_callees",
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
            "list_implementations",
            "list_providers",
        ],
        "guarantees": [
            "CALLS edges: direct invocations visible",
            "INHERITS edges: class hierarchy visible",
            "SPI/reflection dispatch: NOT visible (external_or_dynamic)",
            "All symbols reachable from entry points classified",
        ],
        "limitations": [
            "ServiceLoader registrations not extracted",
            "Reflection-based dispatch invisible",
            "Config-driven routes not captured",
        ],
    },
}
```

### 3. Set Ontology Version During Analysis
Update the `analyze()` function to set ontology_version when creating CodeGraph.

**Location**: `src/lineagelens/analyzer.py`

```python
def analyze(root: Path, config: ProjectConfig | None = None) -> tuple[CodeGraph, AnalysisReport]:
    graph = CodeGraph(str(root))
    graph.ontology_version = "1.0"  # Set current version
    # ... rest of analysis
```

### 4. Serialize/Deserialize Ontology Version
Update JSON schema and serialization to include `ontology_version`.

**Location**: `src/lineagelens/cli.py` (write_artifacts function)

The field will be automatically included in graph.json when CodeGraph is serialized to JSON.

### 5. Update MCP Server Ontology Instructions
Reference ontology version in instructions.

**Location**: `src/lineagelens/mcp_server.py` (already done in Issue #37)

Instructions already state: "This ontology describes **schema version 1.0**..."

Update to be consistent with CodeGraph.ontology_version.

## Testing

### Unit Tests (tests/test_ontology_versioning.py)
```python
class TestOntologyVersioning(unittest.TestCase):
    def test_codegraph_has_ontology_version(self):
        graph = CodeGraph(".")
        self.assertEqual(graph.ontology_version, "1.0")
    
    def test_ontology_version_persists_in_json(self):
        # Write graph to JSON, read back
        # Verify ontology_version field present
        
    def test_ontology_version_1_0_lists_relation_kinds(self):
        version_info = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("CALLS", version_info["relation_kinds"])
        self.assertIn("INHERITS", version_info["relation_kinds"])
        self.assertIn("OVERRIDES", version_info["relation_kinds"])
        self.assertIn("DECORATES", version_info["relation_kinds"])
        self.assertNotIn("PROVIDES", version_info["relation_kinds"])
    
    def test_ontology_version_includes_limitations(self):
        version_info = ONTOLOGY_VERSIONS["1.0"]
        self.assertIn("limitations", version_info)
        # Should mention ServiceLoader, reflection, config-driven
```

### Integration Tests
- Analyze a project, verify graph.ontology_version == "1.0" in output
- Load graph from JSON, verify ontology_version field present

## Schema Changes
- CodeGraph dataclass adds `ontology_version: str = "1.0"` field
- JSON schema version remains at 3 (this is metadata, not schema change)
- Backward compatibility: old graphs without ontology_version can be detected and handled

## Backward Compatibility
- New field has default value "1.0"
- Old JSON files without the field will be upgraded to "1.0" on load
- No breaking changes

## Success Criteria
1. CodeGraph has ontology_version field ✓
2. Default value is "1.0" ✓
3. Ontology version persists in graph.json ✓
4. ONTOLOGY_VERSIONS table documents capabilities ✓
5. MCP instructions reference ontology version ✓
6. Tests verify field presence and content ✓

## Future Work
When new relation kinds are added (e.g., PROVIDES in a future feature):
1. Update ONTOLOGY_VERSIONS["1.1"] to include new capabilities
2. Set CodeGraph.ontology_version = "1.1" during analysis
3. Agents can query version and understand what's available
4. Old graphs remain labeled "1.0" for reference
