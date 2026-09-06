# Spec: Issue #37 - Add MCP Server Ontology Instructions

## Overview
Add a code ontology primer to the LineageLens MCP server initialization via the `instructions=` parameter. This enables Claude agents to understand the graph schema, relation kinds, evidence tiers, and proper query routing.

## Problem Statement
Currently, the MCP server provides tools without context about:
- What relation kinds exist (CALLS, INHERITS, OVERRIDES, DECORATES, PROVIDES)
- How to interpret evidence/resolution fields
- When to use which query tools
- How to handle cases where analysis is incomplete (reflection, SPI dispatch, dynamic registration)

As a result, Claude agents over-apply filtering heuristics or make incorrect assumptions about completeness.

## Solution Design

### 1. Add `instructions=` Parameter to MCPServer
The MCP protocol supports passing server-wide context via the `instructions` parameter during initialization. This is sent once at handshake time and becomes the agent's reference for the server's semantics.

**Location**: `src/lineagelens/mcp_server.py`, line 159

```python
server = MCPServer(
    "lineagelens",
    instructions=_get_ontology_instructions()
)
```

### 2. Create Ontology Instructions Function
Create a function that returns a comprehensive primer covering:

#### 2.1 Relation Kind Semantics
- **CALLS**: Direct invocation (method call, function call)
- **INHERITS**: Class inheritance (extends) or interface implementation (implements)
- **OVERRIDES**: Method override in subclass
- **DECORATES**: Annotation/decorator application
- **PROVIDES**: SPI/service provider registration (from META-INF/services/* or META-INF/dubbo/internal/*)

#### 2.2 Evidence Tiers
- **deterministic_fact**: From literal syntax (observable without inference)
- **deterministic_heuristic**: From name/signature matching (e.g., polymorphic override detection)
- **external_or_dynamic**: Analysis hit a limit (reflection, SPI dispatch, config-driven)

#### 2.3 Resolution Values
- **resolved**: Direct match found in graph
- **resolved_via_inference**: Match via name/signature heuristic
- **external_or_dynamic**: Beyond static analysis (reflection, plugin loading, SPI dispatch)

#### 2.4 Query Routing Table
Maps agent intent to proper tools:
- "Find all callers of X" → `get_callers(X)`
- "Find all things that call X" → `get_callers(X)` + filter by kind="CALLS"
- "Find all implementations of interface I" → `get_callers(I)` + filter by kind="INHERITS"
- "Find all providers of service S" → Use new `list_providers(S)` tool (from issue #38)
- "Trace through method hierarchy" → `get_lineage(X, direction="both")`

#### 2.5 Agent Rules
- When a relation has `resolution="external_or_dynamic"`, do NOT conclude analysis is complete
- When agent reasoning assumes "if X didn't change, consumers don't change", check for internal changes via `get_lineage(X)` 
- Limit speculative reasoning; prefer asking what graph can verify

### 3. Update Tool Docstrings
Enhance docstrings to mention:
- What relation kinds this tool returns
- Whether filtering by kind is recommended
- Known limitations (e.g., SPI files not indexed)

**Affected tools**:
- `get_callers()`: Add "Returns all relation kinds; filter by kind='CALLS' to exclude INHERITS/PROVIDES"
- `get_callees()`: Add "Returns all relation kinds; filter by kind='CALLS' to exclude INHERITS/OVERRIDES"
- Add new: "This tool cannot see SPI/service-provider dispatch; use `list_providers()` for that"

## Implementation

### Files to Modify
1. **src/lineagelens/mcp_server.py**
   - Add `_get_ontology_instructions()` function (~50-80 lines)
   - Update MCPServer initialization to pass `instructions=`
   - Update tool docstrings for get_callers, get_callees
   - Add note about PROVIDES relations and external_or_dynamic resolution

### Testing
1. **Unit Tests** (tests/test_mcp_server.py)
   - Test `_get_ontology_instructions()` returns non-empty string
   - Test instructions contain all key terms (CALLS, INHERITS, PROVIDES, etc.)
   - Test MCPServer initializes with instructions parameter
   
2. **Integration Tests**
   - Verify MCP server accepts connections and exposes instructions
   - No changes to tool behavior; this is purely metadata

## Backward Compatibility
- Zero breaking changes: tools behave identically
- Older agents without ontology awareness work as before
- New agents benefit from ontology instructions

## Success Criteria
1. MCPServer initializes with `instructions=` parameter ✓
2. Instructions document all relation kinds, evidence tiers, resolution values ✓
3. Query routing table helps agents pick correct tools ✓
4. Tool docstrings mention known limitations (SPI, reflection) ✓
5. Tests verify instructions are present and non-empty ✓

## Future Work
- Issue #38: Add `list_implementations()` and `list_providers()` tools for dedicated filtering
- Issue #39: Extract PROVIDES relations from service registries
- Update ontology version when schema changes (Issue #41)
