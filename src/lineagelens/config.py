"""Project configuration; project conventions belong here, never in the analyser."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-5"
    api_key_env: str = "LINEAGELENS_API_KEY"


@dataclass(frozen=True)
class ProjectConfig:
    source_roots: tuple[str, ...] = ("src",)
    test_roots: tuple[str, ...] = ("tests",)
    script_roots: tuple[str, ...] = ("scripts",)
    cron_files: tuple[str, ...] = ()
    docs_globs: tuple[str, ...] = ("**/*.md",)
    frameworks: tuple[str, ...] = ("fastapi", "typer")
    llm: LLMConfig = field(default_factory=LLMConfig)

    @classmethod
    def load(cls, root: Path, config_path: Path | None = None) -> "ProjectConfig":
        path = config_path or root / "lineagelens.yaml"
        if not path.exists():
            return cls()
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("Install PyYAML to load lineagelens.yaml") from exc
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        llm = LLMConfig(**raw.pop("llm", {}))
        keys = {name for name in cls.__dataclass_fields__ if name != "llm"}
        return cls(**{key: tuple(value) if isinstance(value, list) else value for key, value in raw.items() if key in keys}, llm=llm)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
