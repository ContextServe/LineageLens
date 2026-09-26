# LineageLens AI Agent Guidelines

You are equipped with the LineageLens MCP server, providing you with deterministic, compiler-grade codebase graph queries.

**When interacting with this codebase:**
1. **Never guess the architecture or blast radius.** Before proposing to delete or modify a component, use the `impact_of` tool to verify its transitive impact.
2. **Navigate deterministically.** Instead of relying solely on semantic search or reading massive files to understand execution paths, use `find_paths`, `callers_of`, and `callees_of` to query the exact call chains.
3. **Trace data flow.** When investigating where a variable originates or where it is passed downstream, always use `dataflow_of`.
4. **Explain dependencies.** If you are unsure why two components are connected, use `explain` to retrieve the compiler evidence.

Using these tools drastically reduces hallucinations and prevents context-window bloat, ensuring safe and accurate refactoring.
