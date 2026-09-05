# Issue #19 Implementation Spec: MCP Surface Exposure + list_fields_by_type Tool

## Executive Summary
Expose LineageLens graph data and queries via MCP (Model Context Protocol) server, making the codebase introspectable to Claude and other AI models. Implement `list_fields_by_type` tool as the first MCP-exposed capability.

## Why This Matters
**AI-Native Introspection**: Claude and other LLMs need direct access to code structure for:
- Field discovery by type: "What fields of type `Customer` exist?"
- Instance attribute analysis: "Which classes manage this state?"
- Type-driven refactoring: "Show all usages of deprecated type X"

**Extensibility**: MCP surface provides a standard interface for future tools without modifying LineageLens core.

## MCP Architecture

### MCP Server Setup
- **Type**: Stdio-based MCP server
- **Port**: N/A (stdio mode)
- **Protocol**: JSON-RPC 2.0 over stdio
- **Tools exposed**: list_fields_by_type, (future: find_symbols, trace_calls, etc.)

### Tool: list_fields_by_type
**Purpose**: Find all class-level fields/attributes of a given type across the codebase.

**Signature**:
```python
list_fields_by_type(type_name: str, include_subtypes: bool = False) -> list[dict]
```

**Parameters**:
- `type_name` (required): Fully qualified type name (e.g., "java.lang.String", "models.User")
- `include_subtypes` (optional): If true, also include fields of subtypes of the given type (requires MRO traversal)

**Returns**:
```json
[
  {
    "field_id": "com.example.order.Order.customer",
    "field_name": "customer",
    "class_id": "com.example.order.Order",
    "class_name": "Order",
    "type": "com.example.models.Customer",
    "visibility": "private",
    "static": false,
    "line": 15,
    "file": "src/main/java/com/example/order/Order.java",
    "description": "The customer associated with this order"
  },
  ...
]
```

**Error Handling**:
- Type not found: Return empty list (graceful degradation)
- Invalid type format: Return error with message "Invalid type name format"
- Graph not loaded: Return error with message "Graph not loaded; run lineagelens build first"

## Implementation Details

### Part 1: MCP Server Wrapper
**File**: `src/lineagelens/mcp_server.py` (NEW)

```python
from mcp.server import Server
from lineagelens.queries import load_graph
from lineagelens.config import ProjectConfig

class LineageLensMCPServer:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.graph = None  # Lazy-loaded on first query
        self.server = Server("lineagelens")
        self.register_tools()
    
    def register_tools(self):
        @self.server.tool()
        def list_fields_by_type(type_name: str, include_subtypes: bool = False):
            """Find all class fields of a given type."""
            return self.list_fields_by_type(type_name, include_subtypes)
    
    def list_fields_by_type(self, type_name: str, include_subtypes: bool = False) -> list[dict]:
        # Implementation (see Part 2)
        pass

    def run(self):
        """Start the MCP server on stdio."""
        import asyncio
        asyncio.run(self.server.run_stdio())
```

### Part 2: Tool Implementation
**Location**: `src/lineagelens/mcp_server.py` or `src/lineagelens/mcp_tools.py`

**Query Logic**:
```python
def list_fields_by_type(self, type_name: str, include_subtypes: bool = False) -> list[dict]:
    """Find all class fields of a given type."""
    # Lazy-load graph if needed
    if self.graph is None:
        try:
            self.graph = load_graph(self.project_root)
        except FileNotFoundError:
            raise RuntimeError("Graph not loaded; run lineagelens build first")
    
    # Validate type_name format
    if not self._is_valid_type_name(type_name):
        raise ValueError(f"Invalid type name format: {type_name}")
    
    results = []
    
    # Find all symbols that are classes (have fields)
    for symbol in self.graph.symbols.values():
        if symbol.kind != "class":
            continue
        
        # Check each field in the class
        for field_dict in symbol.fields:
            field_type = field_dict.get("type", "unknown")
            
            # Match by exact type or subtype
            if self._type_matches(field_type, type_name, include_subtypes):
                # Build result entry
                field_symbol = self.graph.symbols.get(f"{symbol.id}.{field_dict['name']}")
                results.append({
                    "field_id": f"{symbol.id}.{field_dict['name']}",
                    "field_name": field_dict["name"],
                    "class_id": symbol.id,
                    "class_name": symbol.name,
                    "type": field_type,
                    "visibility": field_symbol.visibility if field_symbol else "unknown",
                    "static": field_symbol.static_ if field_symbol else False,
                    "line": field_dict.get("line", 0),
                    "file": symbol.file,
                    "description": field_symbol.description if field_symbol else None
                })
    
    return results

def _is_valid_type_name(self, name: str) -> bool:
    """Check if type name is properly formatted."""
    # Allow: ClassName, pkg.ClassName, pkg.sub.ClassName, generic types, etc.
    return bool(name) and name.replace(".", "").replace("[", "").replace("]", "").replace("<", "").replace(">", "").replace(",", "").replace(" ", "").isalnum()

def _type_matches(self, field_type: str, search_type: str, include_subtypes: bool) -> bool:
    """Check if field_type matches search_type (exact or subtype)."""
    # Exact match
    if field_type == search_type:
        return True
    
    # Subtype match (if enabled)
    if include_subtypes:
        # Check if field_type is a subclass of search_type
        # This requires MRO traversal via Symbol.bases
        return self._is_subtype_of(field_type, search_type)
    
    return False

def _is_subtype_of(self, candidate: str, parent: str) -> bool:
    """Check if candidate is a subtype of parent (recursive MRO walk)."""
    if candidate == parent:
        return True
    
    candidate_sym = self.graph.symbols.get(candidate)
    if not candidate_sym:
        return False
    
    for base_name in candidate_sym.bases:
        base_sym = self.graph.symbols.get(base_name)
        if base_sym and self._is_subtype_of(base_sym.id, parent):
            return True
    
    return False
```

### Part 3: CLI Integration
**File**: `src/lineagelens/cli.py` (MODIFY)

Add new command:
```python
@click.command()
@click.argument("project", type=click.Path(exists=True))
def mcp(project):
    """Start LineageLens MCP server for AI model integration."""
    from lineagelens.mcp_server import LineageLensMCPServer
    
    project_path = Path(project)
    server = LineageLensMCPServer(project_path)
    server.run()
```

Usage:
```bash
lineagelens mcp /path/to/project
# Server starts on stdio, ready to receive MCP requests
```

### Part 4: Dependencies
**Requirements**: Add to `pyproject.toml` or `setup.py`:
```toml
mcp = "^0.1.0"  # Model Context Protocol library
```

## Testing Strategy

### Unit Tests: `tests/test_mcp_tools.py` (NEW)

**Test Cases**:
1. `test_list_fields_by_type_exact_match` - Find fields of exact type
2. `test_list_fields_by_type_empty_result` - Type not found returns empty list
3. `test_list_fields_by_type_with_subtypes` - Subtype resolution with include_subtypes=True
4. `test_list_fields_by_type_invalid_type_name` - Invalid type format returns error
5. `test_list_fields_by_type_no_graph` - Missing graph.json returns error
6. `test_list_fields_by_type_visibility_captured` - Field visibility correctly reported
7. `test_mcp_server_startup` - Server initializes without errors

### Integration Test
- Mock MCP client
- Send list_fields_by_type request
- Verify response format and content

## Files to Create/Modify

### NEW
- `src/lineagelens/mcp_server.py` - MCP server class
- `src/lineagelens/mcp_tools.py` - Tool implementations (optional, if separated)
- `tests/test_mcp_tools.py` - Unit tests

### MODIFY
- `src/lineagelens/cli.py` - Add `mcp` command
- `pyproject.toml` / `setup.py` - Add mcp dependency
- `README.md` - Document MCP usage

## Success Criteria

✅ MCP server starts without errors
✅ `list_fields_by_type` tool accessible via MCP
✅ All test cases pass (7 tests)
✅ No regressions in existing CLI/build functionality
✅ Tool response format matches spec exactly
✅ Error handling graceful (no crashes on invalid input)
✅ Code review approved

## Future Work (Post-Issue #19)

- `find_symbols(name_pattern)` - Search by symbol name
- `trace_calls(symbol_id, max_depth)` - Trace call chains
- `list_entry_points()` - Find all entry points
- `analyze_reachability(symbol_id)` - Reachability from entry points
- Real-time graph updates (watch for file changes)

## Dependencies

- ✅ Issue #18: Variables/Locals Enrichment (provides Symbol.fields/locals)
- 🔜 Issue #20: Schema version bump (optional, for consistency)

---

## Review Checklist
- [ ] MCP server initializes and runs
- [ ] Tool registration works without errors
- [ ] All test cases pass
- [ ] Response format matches spec
- [ ] Error handling is graceful
- [ ] CLI integration works (`lineagelens mcp`)
- [ ] Code review approved
