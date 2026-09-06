# Spec: Issue #39 - Add PROVIDES Relation Extraction from Service Registries

## Overview
Extract SPI (Service Provider Interface) registrations from registry files into PROVIDES relations in the code graph. This closes the "SPI dispatch blindness" gap identified in the PR #12700 analysis.

## Problem Statement
Currently, LineageLens cannot see SPI/service-provider dispatch because:
- ServiceLoader reads `META-INF/services/<interface>` at runtime
- Dubbo's `@SPI/@Activate` pattern uses `META-INF/dubbo/internal/<interface>`
- Spring's `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`
- These are resource files (not Java source), so no analyzer parses them

Result: Agents see "X has no callers" even though ServiceLoader dispatch targets X at runtime.

## Solution Design

### 1. Registry Format Table Engine (Generic, Data-Driven)
Create a table-based registry extraction system so each format is just a data row, not code.

**Location**: New file `src/lineagelens/registry_formats.py`

```python
# Define formats as declarative data
REGISTRY_FORMATS = [
    {
        "name": "jdk_service_loader",
        "description": "JDK ServiceLoader (META-INF/services)",
        "path_pattern": "META-INF/services/(.+)",  # regex; group 1 = service interface FQN
        "line_format": "bare_class_names",         # one implementation per line, no metadata
        "weight": 10,                               # higher = more specific (used for ambiguity)
        "active": True,
    },
    # More formats in Issue #40 (Dubbo), Issue #40+ (Spring, etc.)
]

class RegistryFormat:
    """Describes how to extract PROVIDES from a registry file format."""
    
    def parse_file(self, file_path: Path, content: str) -> list[dict]:
        """Parse registry file and return provider symbols.
        
        Returns:
            List of {interface_fqn, provider_class_name} dicts
        """
        # Subclasses implement format-specific parsing
        pass
```

### 2. JDK ServiceLoader Format (MVP Implementation)
Implement the simplest registry format first.

**File structure**:
```
META-INF/services/com.example.MyService
```

**Content format**:
```
# Comment lines (ignored)
com.example.impl.ProviderA  # inline comment (stripped)
com.example.impl.ProviderB

# Empty lines allowed
com.example.impl.ProviderC
```

**Parsing logic**:
1. Match files by pattern: `META-INF/services/(.+)`
2. Extract service interface FQN from path: `com.example.MyService`
3. Read file line-by-line
4. Skip comment lines (starting with `#`) and empty lines
5. Strip inline comments (text after `#`)
6. Each remaining line is a provider class name (FQN or short name)
7. Resolve provider name to symbol ID in graph

**Implementation**:

```python
class JdkServiceLoaderFormat(RegistryFormat):
    """Parse JDK ServiceLoader registry files."""
    
    PATTERN = r"META-INF/services/(.+)"
    
    def parse_file(self, file_path: Path, content: str) -> list[dict]:
        """Extract service interface and providers from META-INF/services file."""
        match = re.match(self.PATTERN, str(file_path))
        if not match:
            return []
        
        service_fqn = match.group(1)
        providers = []
        
        for line in content.split("\n"):
            # Strip comments
            if "#" in line:
                line = line[:line.index("#")]
            line = line.strip()
            
            # Skip empty lines
            if not line:
                continue
            
            providers.append({
                "interface_fqn": service_fqn,
                "provider_class": line,
            })
        
        return providers
```

### 3. Integration with Analyzers

Add PROVIDES extraction to the analyzer pipeline. This happens AFTER symbol extraction but BEFORE graph serialization.

**Location**: `src/lineagelens/analyzer.py` (or language-specific analyzers)

**Flow**:
1. Analyzer extracts symbols and CALLS/INHERITS/etc. relations
2. NEW: Scan project for registry files matching known formats
3. Parse each registry file → list of {interface_fqn, provider_class}
4. For each provider:
   - Resolve provider_class to a symbol in the graph
   - Create a PROVIDES relation: provider_symbol → interface_symbol
   - Set evidence="deterministic_fact" (read from literal file)
   - Set resolution="resolved" (provider found) or "resolved_via_inference" (name-matched)
   - Store registry_file in relation.arguments

**Pseudocode**:
```python
def extract_provides_relations(graph: CodeGraph, project_path: Path) -> list[Relation]:
    """Extract PROVIDES relations from service registry files."""
    provides_relations = []
    
    for registry_format in REGISTRY_FORMATS:
        if not registry_format.active:
            continue
        
        # Find all files matching this format
        for file_path in find_registry_files(project_path, registry_format):
            content = file_path.read_text()
            providers = registry_format.parse_file(file_path, content)
            
            for provider_info in providers:
                # Resolve interface symbol
                interface_sym = resolve_symbol(graph, provider_info["interface_fqn"])
                if not interface_sym:
                    continue  # Interface not in graph (external dependency)
                
                # Resolve provider class
                provider_sym = resolve_symbol(graph, provider_info["provider_class"])
                if not provider_sym:
                    # Try name inference (short class name → FQN)
                    provider_sym = resolve_by_name_heuristic(graph, provider_info["provider_class"])
                    if not provider_sym:
                        continue  # Provider not found
                    resolution = "resolved_via_inference"
                else:
                    resolution = "resolved"
                
                # Create relation
                rel = Relation(
                    source=provider_sym.id,
                    target=interface_sym.id,
                    kind="PROVIDES",
                    file=str(file_path),  # registry file, not source file
                    line=0,  # registry files don't have line numbers
                    evidence="deterministic_fact",
                    resolution=resolution,
                    arguments={"registry_file": str(file_path), "registry_format": registry_format.name},
                )
                provides_relations.append(rel)
    
    return provides_relations
```

### 4. Updates to Core Data Structures

No changes to CodeGraph schema. PROVIDES is just another value for Relation.kind.

Update any code that iterates over relation kinds:
- Analyzers emitting relations
- Queries filtering by kind
- Tests checking kind values

### 5. Update Relation Kind Enumeration

If an enum exists for relation kinds, add PROVIDES. Otherwise, document the valid values.

**Location**: Model documentation or code comments

### 6. Testing

#### Unit Tests (tests/test_provides_extraction.py)
```python
class TestJdkServiceLoaderParsing(unittest.TestCase):
    def test_parses_simple_provider_list(self):
        # "impl.A\nimpl.B" → [impl.A, impl.B]
        
    def test_skips_comment_lines(self):
        # "# comment\nimpl.A" → [impl.A]
        
    def test_strips_inline_comments(self):
        # "impl.A # comment" → "impl.A"
        
    def test_extracts_service_interface_from_path(self):
        # META-INF/services/com.example.Service → service_fqn="com.example.Service"
        
    def test_handles_empty_lines(self):
        # "impl.A\n\nimpl.B" → [impl.A, impl.B]

class TestProvidesRelationExtraction(unittest.TestCase):
    def test_creates_provides_relation_for_found_provider(self):
        # Mock graph with interface + provider class
        # extract_provides_relations() → creates PROVIDES relation
        
    def test_marks_resolution_as_resolved_when_provider_found(self):
        # Verify resolution="resolved" for exact match
        
    def test_marks_resolution_as_inferred_for_name_match(self):
        # Verify resolution="resolved_via_inference" for name-only match
        
    def test_skips_providers_not_in_graph(self):
        # Provider class not found → relation not created
        
    def test_sets_evidence_as_deterministic_fact(self):
        # Read from literal file → evidence="deterministic_fact"
        
    def test_stores_registry_file_in_arguments(self):
        # relation.arguments["registry_file"] = path to META-INF/services/*
        
    def test_integration_with_graph_building(self):
        # Full end-to-end: file → relation → graph → query
```

#### Integration Tests
- Full project analysis with ServiceLoader registrations
- Verify PROVIDES relations appear in get_callers() results
- Verify list_providers() tool returns providers

### 7. Backward Compatibility
- No breaking changes to existing queries or tools
- PROVIDES relations are additional data (subset of all relations)
- Existing code filtering by kind != PROVIDES is unaffected
- Schema version remains unchanged (PROVIDES is new relation kind, not schema change)

## Implementation Plan

### Phase 1: Core Infrastructure (This Issue)
1. Create `src/lineagelens/registry_formats.py`
   - RegistryFormat abstract class
   - JdkServiceLoaderFormat implementation
   - REGISTRY_FORMATS table

2. Update `src/lineagelens/analyzer.py`
   - Add extract_provides_relations() function
   - Call after symbol extraction
   - Merge PROVIDES relations into graph.relations

3. Add tests:
   - test_provides_extraction.py (12-15 tests)
   - Integration test with real ServiceLoader file

### Phase 2: Framework Support (Issue #40+)
- Add DubboSpiFormat (META-INF/dubbo/internal/*)
- Add SpringBootFormat (spring.factories)
- Extend REGISTRY_FORMATS table

## Success Criteria
1. Extract PROVIDES relations from META-INF/services/* ✓
2. Create PROVIDES relations with correct source/target ✓
3. Set evidence="deterministic_fact" for file-sourced relations ✓
4. Set resolution based on symbol resolution status ✓
5. Store registry_file in relation.arguments ✓
6. list_providers() tool returns non-empty results ✓
7. All tests passing ✓

## Future Work
- Issue #40: Add Dubbo SPI format
- Post-#40: Add Spring Boot, Spring, other frameworks
- Consider: Plugin discovery, explicit instantiation patterns
