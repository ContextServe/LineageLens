# Code Change Analysis — Baseline Scenario

You are analyzing a GitHub pull request using standard code exploration tools (Read, Glob, Bash, Grep, etc.).

## Pull Request

**Title:** {pr_title}

**Body:**

{pr_body}

## Your Task

Analyze the PR by exploring the codebase directly:

1. **Understand the overall goal** of the PR from the title and description
2. **Search for affected modules** by grep, file search, and reading key files
3. **Identify entry points** by reading core module definitions and public APIs
4. **Analyze dependencies** by reading import statements and following the code
5. **Determine implementation approach** by understanding existing patterns in the codebase

You have full access to:
- **Read** — examine file contents
- **Glob** — search for files by pattern
- **Grep** — search file contents for keywords
- **Bash** — run find, ls, and other exploration commands

Use your knowledge of software architecture and the codebase structure to infer what needs to change.

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
