"""Tier-3 LLM enrichment (probabilistic, opt-in, completely separate from deterministic graph)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import LLMConfig


@dataclass(frozen=True)
class EnrichmentEntry:
    """A probabilistic/LLM-generated enrichment for a symbol."""

    symbol_id: str
    summary: str  # e.g., generated method/class description
    evidence: str = "llm"  # Always "llm" for tier-3 entries
    model: str = ""  # e.g., "gpt-4-turbo"
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return asdict(self)


@dataclass(frozen=True)
class EnrichmentReport:
    """Summary of enrichment results."""

    project_root: str
    generated_at: str
    entries: list[EnrichmentEntry] = field(default_factory=list)
    model_used: str = ""
    api_calls_made: int = 0
    tokens_spent: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        d = asdict(self)
        d["entries"] = [e.to_dict() for e in self.entries]
        return d


def load_enrichment(project: Path) -> dict[str, EnrichmentEntry]:
    """Load enrichment data from .lineagelens/enrichment.json if it exists.

    Returns empty dict if file doesn't exist (enrichment is optional).
    """
    path = project / ".lineagelens" / "enrichment.json"
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("entries", [])
        return {e["symbol_id"]: EnrichmentEntry(**e) for e in entries}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}  # Silently ignore malformed enrichment; it's optional


def write_enrichment(project: Path, report: EnrichmentReport) -> Path:
    """Write enrichment data to .lineagelens/enrichment.json.

    Returns the path written to.
    """
    path = project / ".lineagelens" / "enrichment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return path


async def enrich_symbols(
    symbol_ids: list[str],
    symbol_details: dict[str, dict[str, Any]],
    config: LLMConfig,
) -> EnrichmentReport:
    """Generate LLM summaries for a list of symbols.

    This is a stub interface. Full implementation requires an async LLM client
    (e.g., httpx) and would make actual API calls to the configured provider.

    For now, returns an empty report to satisfy the interface.

    Args:
        symbol_ids: List of symbol IDs to enrich (e.g., ["app.api.route", "app.models.User"])
        symbol_details: Dict mapping symbol_id → {name, kind, docstring, inputs, outputs}
        config: LLMConfig with provider, base_url, model, api_key_env

    Returns:
        EnrichmentReport with generated EnrichmentEntry objects
    """
    # Future implementation:
    # 1. Read API key from config.api_key_env environment variable
    # 2. Build prompts for each symbol (docstring → summary)
    # 3. Call the LLM endpoint (httpx.AsyncClient)
    # 4. Parse responses and create EnrichmentEntry objects
    # 5. Return a populated EnrichmentReport

    # For now, stub: no LLM calls, return empty report
    return EnrichmentReport(
        project_root="<unknown>",
        generated_at=datetime.utcnow().isoformat(),
        entries=[],
        model_used=config.model,
        api_calls_made=0,
        tokens_spent=0,
    )
