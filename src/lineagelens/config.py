"""Project configuration; project conventions belong here, never in the analyser."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for optional LLM enrichment (Tier 3, probabilistic).

    This config only applies to opt-in LLM-powered features like generating
    method descriptions. It is NOT used for the deterministic analysis.
    Users must explicitly enable LLM features and provide a valid API key.
    """
    provider: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4-turbo"  # Fixed from "gpt-5" (which doesn't exist)
    api_key_env: str = "LINEAGELENS_API_KEY"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8717


@dataclass(frozen=True)
class OutputConfig:
    directory: str = ".lineagelens"
    filename: str = "graph.json"

    @property
    def relative_path(self) -> str:
        return f"{self.directory}/{self.filename}"


@dataclass(frozen=True)
class RiskRule:
    category: str
    severity: str
    match_words: tuple[str, ...]
    only_in_async: bool = False


@dataclass(frozen=True)
class AnalysisConfig:
    risk_rules: tuple[RiskRule, ...] = (
        RiskRule("data_write", "review", ("execute", "insert", "update", "delete", "write", "save", "commit")),
        RiskRule("blocking_in_async", "high", ("requests.", "time.sleep", "subprocess."), only_in_async=True),
    )
    entry_points: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        "api_route": (".get", ".post", ".put", ".patch", ".delete", ".websocket"),
        "cli_command": (".command", ".callback"),
        "framework_callback": (".middleware", ".exception_handler", ".on_event", "validator", "field_validator", "model_validator"),
    })

    # Type inference via Jedi. Only consulted when cheap static resolution fails,
    # and only when the call's trailing name could possibly match an in-repo symbol,
    # so the cost is a small fraction of the call sites. Disable to trade a little
    # resolution for speed, e.g. on PR-time CI runs.
    jedi: bool = True

    # Hard ceiling on inference calls per analysis. None means unbounded. On exceed,
    # one warning is recorded and the engine is not consulted again -- a backstop for
    # pathological repos, not a tuning knob.
    jedi_max_calls: int | None = None


@dataclass(frozen=True)
class ProjectConfig:
    source_roots: tuple[str, ...] = ("src",)
    test_roots: tuple[str, ...] = ("tests",)
    script_roots: tuple[str, ...] = ("scripts",)
    cron_files: tuple[str, ...] = ()
    docs_globs: tuple[str, ...] = ("**/*.md",)
    frameworks: tuple[str, ...] = ("fastapi", "typer")
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @classmethod
    def load(cls, root: Path, config_path: Path | None = None) -> ProjectConfig:
        import yaml

        path = config_path or root / "lineagelens.yaml"
        if not path.exists():
            return cls()
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        llm = LLMConfig(**raw.pop("llm", {}) or {})
        server = ServerConfig(**raw.pop("server", {}) or {})
        output = OutputConfig(**raw.pop("output", {}) or {})
        analysis = cls._load_analysis(raw.pop("analysis", {}) or {})
        keys = {name for name in cls.__dataclass_fields__ if name not in {"analysis", "output", "server", "llm"}}
        rest = {key: tuple(value) if isinstance(value, list) else value for key, value in raw.items() if key in keys}
        return cls(**rest, analysis=analysis, output=output, server=server, llm=llm)

    @staticmethod
    def _load_analysis(raw: dict[str, Any]) -> AnalysisConfig:
        rules = []
        for item in raw.get("risk_rules", []) or []:
            rules.append(RiskRule(
                category=item["category"],
                severity=item["severity"],
                match_words=tuple(item["match_words"]),
                only_in_async=bool(item.get("only_in_async", False)),
            ))
        entry_points = {key: tuple(values) for key, values in (raw.get("entry_points", {}) or {}).items()}
        defaults = AnalysisConfig()
        max_calls = raw.get("jedi_max_calls", defaults.jedi_max_calls)
        return AnalysisConfig(
            risk_rules=tuple(rules) or defaults.risk_rules,
            entry_points=entry_points or defaults.entry_points,
            jedi=bool(raw.get("jedi", defaults.jedi)),
            jedi_max_calls=int(max_calls) if max_calls is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
