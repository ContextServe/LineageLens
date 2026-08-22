# LineageLens for Claude Code (MCP)

Enable Claude to understand Python codebases by installing LineageLens as an MCP server.

## What is this?

LineageLens exposes 9 tools via the Model Context Protocol (MCP) that let Claude:
- Query code structure without reading full source files
- Run impact analysis (what breaks if I change this?)
- Understand module organization safely
- Identify entry points and risk signals
- Make informed refactoring decisions with ~10x fewer tokens

## Installation

### 1. Install LineageLens with MCP Support

```bash
# Using conda (recommended)
conda activate your-env
pip install lineagelens[mcp]

# Or with pip
pip install lineagelens[mcp]
```

### 2. Configure Claude Code

Add LineageLens to your Claude Code settings by editing your `.claude/settings.json`:

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "lineagelens-mcp",
      "env": {
        "LINEAGELENS_PROJECT": "${workspaceFolder}"
      }
    }
  }
}
```

**Or for a specific project:**

```json
{
  "mcpServers": {
    "lineagelens": {
      "command": "lineagelens-mcp",
      "env": {
        "LINEAGELENS_PROJECT": "/path/to/your/project"
      }
    }
  }
}
```

### 3. Restart Claude Code

Close and reopen Claude Code. You should see "lineagelens" in the MCP servers list.

## First Time Setup

Before Claude can help, analyze your codebase:

```bash
cd /path/to/your/project
lineagelens analyze .
```

This creates `.lineagelens/graph.json` with the code structure.

## Available Tools

Claude has access to these 9 tools:

### `get_symbol(project, symbol_id)`
Get full details for a specific symbol (function, class, method).

Example:
```
"Get the full signature and description of app.api.fetch_user"
```

### `search_symbols(project, text, kind=None, limit=30)`
Search for symbols by name or ID substring.

Example:
```
"Find all functions with 'cache' in the name"
```

### `get_callers(project, symbol_id)`
Find all symbols that call this one (direct callers).

Example:
```
"Who calls the User.find method?"
```

### `get_callees(project, symbol_id)`
Find all symbols this one calls (direct callees).

Example:
```
"What does fetch_user call?"
```

### `get_lineage(project, symbol_id, direction="forward", max_depth=5)`
Get the full transitive call chain (forward or backward).

Example:
```
"Show me the complete call chain from fetch_user to all functions it eventually calls"
```

### `impact_analysis(project, symbol_id, max_depth=10)` ⭐ MOST USEFUL
The **critical** tool for safe refactoring: shows everything that would be affected by a change.

Example:
```
"What would break if I deleted the execute_sql function? Show me all affected entry points."
```

### `get_module_overview(project, module)`
High-level overview of a module (for safe exploration without reading full source).

Example:
```
"Give me an overview of the app.models module"
```

### `list_entry_points(project, kind=None)`
Find all entry points (API routes, CLI commands, tests).

Example:
```
"List all API routes in this codebase"
```

### `list_resiliency_risks(project, min_severity=None)`
Find all risk signals (data writes, blocking operations, etc.).

Example:
```
"Show me all high-severity resiliency issues"
```

## Recommended Workflow

Claude will use LineageLens automatically when it's helpful. A typical workflow:

1. **Explore**: "Show me an overview of the app.api module"
   - Claude calls `get_module_overview` to understand structure

2. **Deep Dive**: "What does the fetch_user function do?"
   - Claude calls `get_symbol` for full details

3. **Impact Check**: "What would break if I refactored fetch_user?"
   - Claude calls `impact_analysis` to see blast radius
   - Claude sees which entry points would be affected

4. **Safe Changes**: Based on the data, Claude makes informed suggestions
   - Only reads raw source for the exact symbols being changed
   - Understands dependencies without loading entire codebase

5. **Verify**: "Did any tests touch the code I changed?"
   - Claude uses `get_callers` to find test entries calling the changed code

## Example Conversations

### Scenario 1: Understand a Complex Module

**You:**
```
I'm new to the codebase. Help me understand the app.api module.
```

**Claude:**
- Calls `get_module_overview("app.api")`
- Returns: list of functions, classes, entry points, submodules
- Claude explains the structure in plain English

### Scenario 2: Safe Refactoring

**You:**
```
I want to refactor the User.find method to cache results. 
What would be affected?
```

**Claude:**
- Calls `impact_analysis("app.models.User.find")`
- Returns: all symbols that would be affected + affected entry points
- Claude shows you the blast radius before you make changes

### Scenario 3: Find Risk Signals

**You:**
```
Show me all data writes in the codebase and whether they could cause issues.
```

**Claude:**
- Calls `list_resiliency_risks()`
- Returns: all data_write signals with locations
- Claude groups and prioritizes by risk

## Environment Variables

```bash
# Project to analyze (required)
LINEAGELENS_PROJECT=/path/to/your/project

# Optional: path to lineagelens.yaml (if not in project root)
LINEAGELENS_CONFIG=/path/to/lineagelens.yaml
```

## Troubleshooting

**"Graph not found"**
- Run: `lineagelens analyze /path/to/project`
- Check: `.lineagelens/graph.json` exists

**"Symbol not found"**
- Make sure symbol ID is fully qualified (e.g., `myapp.module.Class.method`)
- Use `search_symbols()` to find the exact ID

**"MCP server not connected"**
- Restart Claude Code
- Check: `lineagelens-mcp` command is in PATH
- Try: `which lineagelens-mcp` to verify installation

**"Impact analysis shows 0 affected symbols"**
- The symbol might not be called by anything (dead code!)
- Check backward direction: `get_lineage(..., direction="backward")`

## Cost

**FREE!** Everything runs locally on your machine:
- No cloud dependencies
- No API calls to external services
- No token costs
- All analysis stays on your device

## Advanced: Custom Configuration

Create `lineagelens.yaml` in your project root to customize:

```yaml
source_roots: [src, lib]
test_roots: [tests, test]
script_roots: [scripts]
frameworks: [fastapi, django, flask]
analysis:
  risk_rules:
    - category: data_write
      severity: review
      match_words: [execute, insert, update, delete, save]
    - category: blocking_in_async
      severity: high
      match_words: [requests., time.sleep, subprocess.]
      only_in_async: true
```

Then re-run: `lineagelens analyze .`

## Tips for Maximum Effectiveness

1. **Ask about impact first**: "What breaks if I change X?" before you change anything
2. **Use module overviews**: "Show me an overview of [module]" before diving into code
3. **Trust the entry points**: Focus on understanding how the public API is used
4. **Check for risks**: "Show me high-severity resiliency issues in [module]"
5. **Lean on the tools**: Claude can process the structured data faster than reading source

## Limitations

- Only understands **Python** code (not JS, Java, etc.)
- Requires AST-parseable Python (will skip syntax errors)
- Dynamic code (eval, reflection) won't be in the graph
- External library calls show as `external:module` (not resolved)

## Integration with Claude Code Projects

Use alongside Claude Code's file editing:

```
Claude: "I see the issue. The fetch_user function calls execute_sql, 
which is flagged as a data_write risk. Let me check the impact..."

[Claude queries impact_analysis]

Claude: "Changing this would affect 3 API routes. Let me refactor safely..."

[Claude edits the files]
```

## Support

- Main README.md for CLI docs
- docs/deploy-chatgpt-actions.md for ChatGPT integration
- GitHub Issues for bugs or feature requests
