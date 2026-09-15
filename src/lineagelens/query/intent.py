"""Query intent detection and routing.

Classifies what the agent is actually trying to do so we can route to the
appropriate tool (search vs. explore vs. get_symbol vs. callers_of).
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import GraphStore


class QueryIntent(Enum):
    """What the agent is trying to accomplish."""

    SEARCH = "search"
    """Find symbols defined in this project by name/docstring/signature."""

    EXTERNAL_USAGE = "external_usage"
    """Find where external packages/symbols are used in this project."""

    SYMBOL_LOOKUP = "symbol_lookup"
    """Get detailed info about a specific symbol."""

    TRACE_FLOW = "trace_flow"
    """Trace call chains or data flow."""

    EXPLORE = "explore"
    """High-level exploration: "What's relevant to this query?"."""


def detect_intent(query: str, store: GraphStore | None = None) -> QueryIntent:
    """Classify a query to determine the best tool.

    Args:
        query: User query string
        store: Graph store (for checking if symbols exist). Optional for keyword-based detection.

    Returns:
        QueryIntent recommending which tool to use

    Examples:
        "ChatAnthropic" → SEARCH (local symbol)
        "anthropic.ChatAnthropic" → EXTERNAL_USAGE (external package)
        "how does anthropic get used" → EXPLORE (high-level)
        "what calls _create" → TRACE_FLOW (call analysis)
    """

    # Normalize
    q = query.lower().strip()

    # 1. Check for explicit intent keywords
    flow_keywords = {"call", "where", "how", "who", "what reach", "depend", "impact", "calls"}
    if any(kw in q for kw in flow_keywords):
        return QueryIntent.TRACE_FLOW

    # 2. Check for package.symbol pattern (external)
    if "." in query and any(pkg in q for pkg in ["anthropic.", "openai.", "langchain.", "pydantic."]):
        return QueryIntent.EXTERNAL_USAGE

    # 3. Check if it's a known external package name
    external_packages = {
        "anthropic", "openai", "pydantic", "fastapi", "flask", "requests",
        "numpy", "pandas", "pytorch", "tensorflow", "sklearn", "aiohttp"
    }
    if any(pkg in q for pkg in external_packages):
        return QueryIntent.EXTERNAL_USAGE

    # 4. Check if symbol exists locally (if store provided)
    if store is not None:
        local_matches = store.search(query, limit=1)
        if local_matches:
            return QueryIntent.SEARCH

    # 5. Check if it's unambiguously a single symbol (qualified name)
    if "/" not in query and " " not in query and len(query) > 3 and store is not None:
        by_qname = store.nodes_by_qualified_name(query)
        if by_qname:
            return QueryIntent.SYMBOL_LOOKUP

    # 6. Default: high-level exploration
    return QueryIntent.EXPLORE


def suggest_tool(intent: QueryIntent) -> str:
    """Generate agent guidance for which tool to use."""

    suggestions = {
        QueryIntent.SEARCH: (
            "Try: search('ChatAnthropic') to find symbols defined in this project"
        ),
        QueryIntent.EXTERNAL_USAGE: (
            "Try: explore('anthropic') to see how external packages are used in this project"
        ),
        QueryIntent.SYMBOL_LOOKUP: (
            "Try: get_symbol('ChatAnthropic._create') for detailed info"
        ),
        QueryIntent.TRACE_FLOW: (
            "Try: callers_of('_create') or trace_flow('_generate')"
        ),
        QueryIntent.EXPLORE: (
            "Try: explore() to get everything relevant in one call"
        ),
    }

    return suggestions.get(intent, "Try: explore() for a high-level overview")
