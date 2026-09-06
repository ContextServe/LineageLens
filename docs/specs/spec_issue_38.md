# Spec: Issue #38 - Add list_implementations and list_providers MCP Tools

## Overview
Add two dedicated MCP tools for querying filtered relation types:
- `list_implementations(interface_id)`: Find all classes implementing an interface
- `list_providers(service_id)`: Find all providers of an SPI service (once PROVIDES relations exist)

These simplify common queries by filtering `get_callers()` results by relation kind, making agent intent explicit.

## Problem Statement
Currently, agents must use `get_callers()` and manually filter by `relation.kind`:
```python
# Current (implicit filtering)
callers = get_callers("MyInterface")
implementations = [r for r in callers if r.kind == "INHERITS"]
```

This is:
1. Requires agents to know the internal data structure
2. Easy to forget filtering, leading to mixed CALLS/INHERITS results
3. Hard to discover that "who implements X" requires filtering `get_callers()` by kind

## Solution Design

### 1. Add `list_implementations(interface_id)` Tool
**Purpose**: Find all classes that directly or transitively implement an interface.

**Location**: `src/lineagelens/mcp_server.py`, after `get_impact()` tool

**Parameters**:
- `interface_id`: Symbol ID of the interface (e.g., "java.io.Serializable")

**Returns**:
```python
{
    "interface_id": "java.io.Serializable",
    "count": 3,
    "implementations": [
        {
            "id": "com.example.MyClass",
            "kind": "class",
            "name": "MyClass",
            "file": "src/main/java/com/example/MyClass.java",
            "line": 10,
            "module": "com.example",
            "resolution": "resolved",
            "evidence": "deterministic_fact"
        },
        ...
    ]
}
```

**Implementation Logic**:
1. Call `query_get_callers(graph, interface_id, ...)` 
2. Filter relations where `relation.kind == "INHERITS"`
3. Extract target symbols (the implementing classes)
4. Return with symbol metadata

### 2. Add `list_providers(service_id)` Tool
**Purpose**: Find all providers of an SPI or service interface.

**Location**: `src/lineagelens/mcp_server.py`, after `list_implementations()`

**Parameters**:
- `service_id`: Symbol ID of the service interface

**Returns**:
```python
{
    "service_id": "org.apache.dubbo.rpc.Protocol",
    "count": 2,
    "providers": [
        {
            "id": "org.apache.dubbo.rpc.protocol.ProtocolFilterWrapper",
            "kind": "class",
            "name": "ProtocolFilterWrapper",
            "file": "...",
            "line": 15,
            "module": "...",
            "relation_kind": "PROVIDES",
            "resolution": "resolved",
            "evidence": "deterministic_fact",
            "registry_source": "META-INF/services/org.apache.dubbo.rpc.Protocol"
        },
        ...
    ]
}
```

**Implementation Logic**:
1. Call `query_get_callers(graph, service_id, ...)`
2. Filter relations where `relation.kind == "PROVIDES"`
3. Extract provider symbols
4. Include registry_source if available (from Issue #39 implementation)

**Note**: Until Issue #39 is implemented, this tool will return empty results because PROVIDES relations don't exist yet. That's acceptable; the tool is ready for the data once #39 adds it.

### 3. Update `queries.py` (if needed)
No changes to `queries.py` are required. The tools will use existing `query_get_callers()` and filter results in-process.

## Testing

### Unit Tests (tests/test_mcp_tools.py)
Create new test file or add to existing test suite:

```python
class TestListImplementations(unittest.TestCase):
    def test_list_implementations_finds_implementers(self):
        # Build test graph with interface + 2 implementing classes
        # Call list_implementations()
        # Verify count=2, all have kind="INHERITS"
        
    def test_list_implementations_empty_for_interface_with_no_implementers(self):
        # Call list_implementations() on interface with no implementers
        # Verify count=0, implementations=[]
        
    def test_list_implementations_includes_symbol_metadata(self):
        # Verify each result has id, kind, name, file, line, module, resolution, evidence

class TestListProviders(unittest.TestCase):
    def test_list_providers_returns_empty_before_provides_extraction(self):
        # Until Issue #39, PROVIDES relations don't exist
        # Verify count=0, providers=[]
        # This confirms the tool is ready for future data
        
    def test_list_providers_includes_registry_source(self):
        # (After Issue #39)
        # Verify each provider includes registry_source metadata
```

## Backward Compatibility
- Zero breaking changes: new tools, existing tools unchanged
- Older agents continue using `get_callers()` + manual filtering
- New agents benefit from explicit tool names

## Dependencies
- Depends on Issue #37 (ontology instructions) for agent awareness
- Will be enhanced by Issue #39 (PROVIDES relation extraction)
- Issue #40 (Dubbo registry format) will expand PROVIDES data coverage

## Success Criteria
1. `list_implementations(interface_id)` returns all INHERITS relations for interface ✓
2. `list_implementations()` includes full symbol metadata ✓
3. `list_providers(service_id)` returns empty until Issue #39 ✓
4. Tool docstrings clearly explain relation filtering ✓
5. Unit tests cover both tools ✓

## Future Work
- Issue #39: Implement PROVIDES relation extraction
- Issue #40: Add Dubbo SPI format support
- Issue #41: Add ontology versioning
