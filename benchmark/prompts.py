"""Registry-driven prompt generation for benchmark suite.

Replaces hardcoded prompt files with templates that are rendered
per-arm using hints from the arm registry.
"""

from pathlib import Path

from arms import Arm


def render_prompt(
    template_path: Path,
    arm: Arm,
    suite: str = "pr",
    **task_fields,
) -> str:
    """Load a prompt template and render it with arm-specific placeholders.

    Args:
        template_path: Path to the template file
        arm: The arm to tailor the prompt for
        suite: "pr" (PR-replication) or "arch" (architecture-Q&A)
        **task_fields: Task-specific fields (pr_title, pr_body, question, etc.)

    Returns:
        Fully rendered prompt string
    """
    with open(template_path, "r") as f:
        template_text = f.read()

    # Build arm-specific fields
    tool_only_constraint = ""
    if arm.mcp_server is not None:
        tool_only_constraint = (
            "**CRITICAL CONSTRAINT:** You must not read the source code directly, explore files "
            "manually, or use any prior knowledge of the repository structure. All decisions must be "
            "derived EXCLUSIVELY from the tool outputs."
        )

    pr_tool_hint = ""
    if suite == "pr" and "list_prs" in arm.tool_names:
        pr_tool_hint = (
            f"This tool set includes PR-native capabilities ({', '.join([t for t in arm.tool_names if 'pr' in t.lower()])}). "
            "Use them to analyze pull request impact directly."
        )

    # Build render context
    context = {
        "tool_only_constraint": tool_only_constraint,
        "tool_hints": arm.tool_hints,
        "pr_tool_hint": pr_tool_hint,
        **task_fields,
    }

    # Render using simple .format()
    rendered = template_text.format(**context)
    return rendered
