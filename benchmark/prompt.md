# Code Change Analysis

You are an expert code architect analyzing a GitHub pull request. Your task is to produce a detailed implementation plan for the changes described below.

## Pull Request

**Title:** {pr_title}

**Body:**

{pr_body}

## Your Analysis

Using the codebase and your knowledge of software architecture, analyze the pull request and provide:

1. **Summary of Changes** — What is the overall goal of this PR?
2. **Affected Modules** — Which modules, files, and components are impacted?
3. **Key Entry Points** — What are the main entry points that users/code will interact with?
4. **Dependencies & Integration** — How do the changes integrate with existing code? What are the dependencies?
5. **Implementation Approach** — How would you implement these changes? What is the order of operations?
6. **Risks & Breaking Changes** — Are there any risks, edge cases, or breaking changes to consider?

## Important

**At the end of your answer, provide a structured list of files you would need to change.** Format it exactly like this:

```
## Files I would change
path/to/file1.py
path/to/file2.py
libs/core/langchain_core/some_module.py
... (one relative path per line)
```

Do not include any explanation after this section—just the file paths, one per line.
