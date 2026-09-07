# Code Change Analysis — {suite_name}

You are analyzing a GitHub pull request.

{tool_only_constraint}

## Pull Request

**Title:** {pr_title}

**Body:**

{pr_body}

## Your Task

Analyze the PR to understand its scope and impact:

1. **Understand the overall goal** from the title and description
2. **Identify affected modules** and their dependencies
3. **Understand existing patterns** in the codebase
4. **Determine what files need to change** based on the implementation

{tool_hints}

{pr_tool_hint}

## Output Format

At the end of your answer, provide a structured list of files you would need to change:

```
## Files I would change
path/to/file1.py
path/to/file2.py
libs/core/langchain_core/some_module.py
... (one relative path per line)
```

Do not include any explanation after this section—just the file paths, one per line.
