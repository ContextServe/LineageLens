# Code Change Analysis — MCP-TOOL-ONLY Scenario

You are analyzing a GitHub pull request **using ONLY the LineageLens MCP tools provided**. 

**CRITICAL CONSTRAINT:** You must not read the source code directly, explore files manually, or use any prior knowledge of the repository structure. All decisions must be derived EXCLUSIVELY from the LineageLens MCP tool outputs (code graph queries, symbol relationships, file dependencies).

## Pull Request

**Title:** {pr_title}

**Body:**

{pr_body}

## Your Task

Using ONLY LineageLens MCP tools (ignore Read, Glob, Bash, or any other file exploration tools):

1. **Query the code graph** to understand the PR's impact
2. **Identify affected modules** by following symbol relationships in the graph
3. **Find entry points** by querying for public APIs and exports
4. **Analyze dependencies** using the graph's dependency edges
5. **Determine implementation order** based on call dependencies

Do not:
- Open or read any source files
- Use prior knowledge of this repository
- Make assumptions about file locations or module names
- Rely on repository structure intuition

Instead:
- Use `query_code_graph` to find symbols and their relationships
- Use `get_symbol_references` to see what depends on what
- Use `get_symbol_callers` to understand call chains
- Trust the graph as the single source of truth

## Output Format

At the end of your answer, provide a structured list of files you would need to change. Format it exactly like this:

```
## Files I would change
path/to/file1.py
path/to/file2.py
libs/core/langchain_core/some_module.py
... (one relative path per line)
```

Do not include any explanation after this section—just the file paths, one per line.
