"""Opt-in usage telemetry and MCP cost metering (#59).

One subsystem, two consumers: anonymous usage events for the open-source CLI,
and per-call metering at the MCP boundary that is the substrate for per-repo and
per-branch cost attribution. Building them separately would produce two event
pipelines that disagree.

The MCP boundary is the right meter, and not incidentally. Query cost grows with
the **number of calls**, not the size of any one answer -- which is why the tool
descriptions already steer agents toward ``explore()`` rather than a fan-out of
single-symbol calls. So the boundary where an agent's tokens are spent is
exactly the boundary where they can be counted and reduced.

Four rules, in the order they matter.

**Off until explicitly enabled.** No events before ``telemetry enable``.
``DO_NOT_TRACK`` and ``CI`` are hard overrides that configuration cannot
re-enable -- a preference file should not be able to opt a build server in.

**Never blocks, never fails a command.** Buffered in memory, flushed once at
exit, bounded timeout, no foreground retries, every exception swallowed to
debug. A telemetry outage must not be able to break ``index``.

**Counts and versions, never content.** No paths, symbol names, repository
names, remotes, branches or source text in an anonymous event. A repository name
is identifying; a language count is not. Authenticated events add identity, and
only ever reach the environment the user logged into.

**The install id is random, not derived.** A value computed from hostname,
username or project path is a fingerprint wearing an anonymity label.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Where the preference and the anonymous install id live. Beside credentials,
#: not in the project: a telemetry choice is per user, not per repository, and
#: committing one would opt a whole team in by accident.
PREFS_FILENAME = "telemetry.json"

#: Anonymous OSS endpoint.
OSS_PATH = "/api/v1/telemetry/usage"

#: Authenticated metering endpoint, matching the server's existing route.
METERING_PATH = "/api/v1/telemetry/tokens"

#: Bytes per token, for converting a response size into an estimate. A rough
#: average across English text and code; the point is a consistent yardstick,
#: not accuracy. Anything derived from it is named ``estimated_*``.
BYTES_PER_TOKEN = 4

#: Hard caps. A flush that hangs would turn telemetry into an availability
#: problem for the command it is attached to.
FLUSH_TIMEOUT_SECONDS = 3.0
MAX_BUFFERED_EVENTS = 500

#: Environment variables that switch telemetry off regardless of configuration.
#: `DO_NOT_TRACK` is the cross-tool convention; `CI` is here because a build
#: server cannot consent on behalf of the person who wrote the config.
KILL_SWITCHES = ("DO_NOT_TRACK", "CI")

#: Fields an anonymous event may never contain, asserted over the serialised
#: payload rather than reviewed by eye -- so a field added later by someone who
#: did not read this module still fails the test.
FORBIDDEN_OSS_KEYS = (
    "path", "file", "file_path", "project_root", "repo", "repo_name",
    "remote", "branch", "commit_sha", "symbol", "qualified_name", "name",
    "signature", "docstring", "source", "context_line", "candidates",
)


def prefs_path() -> Path:
    from .credentials import _creds_path

    return _creds_path().parent / PREFS_FILENAME


def _read_prefs() -> dict[str, Any]:
    try:
        return json.loads(prefs_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _write_prefs(data: dict[str, Any]) -> None:
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)
    # Owner-only: the file holds a stable identifier, and a world-readable one
    # is a slightly worse secret than no identifier at all.
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def suppressed_by() -> str | None:
    """The environment variable switching telemetry off, if any."""
    for name in KILL_SWITCHES:
        value = os.environ.get(name, "")
        if value and value.lower() not in ("0", "false", "no"):
            return name
    return None


def is_enabled() -> bool:
    """Whether anonymous telemetry may be sent right now.

    A kill switch wins over configuration. Being authenticated is *not* consent
    for anonymous OSS telemetry: logging in to a hosted service is consent for
    that service, and conflating the two would opt users in by side effect.
    """
    if suppressed_by():
        return False
    return bool(_read_prefs().get("enabled", False))


def install_id() -> str:
    """A random, stable, per-user identifier.

    Generated once and stored. Never derived from anything about the machine --
    a derived id is a fingerprint, and calling it anonymous does not change
    that.
    """
    prefs = _read_prefs()
    existing = prefs.get("install_id")
    if existing:
        return str(existing)
    fresh = str(uuid.uuid4())
    prefs["install_id"] = fresh
    _write_prefs(prefs)
    return fresh


def set_enabled(enabled: bool) -> None:
    prefs = _read_prefs()
    prefs["enabled"] = bool(enabled)
    prefs.setdefault("install_id", str(uuid.uuid4()))
    _write_prefs(prefs)


def status() -> dict[str, Any]:
    """What is sent, and where. Includes the real payload, not a description."""
    prefs = _read_prefs()
    return {
        "enabled": bool(prefs.get("enabled", False)),
        "suppressed_by": suppressed_by(),
        "effective": is_enabled(),
        "install_id": prefs.get("install_id"),
        "preferences_file": str(prefs_path()),
        "endpoint": OSS_PATH,
        "never_sent": list(FORBIDDEN_OSS_KEYS),
        "example_event": example_event(),
    }


def example_event() -> dict[str, Any]:
    """A real event, so `telemetry status` shows bytes rather than prose."""
    return usage_event(command="index", duration_ms=1234, exit_code=0)


def _environment() -> dict[str, Any]:
    from .core import SCHEMA_VERSION

    return {
        "schema_version": SCHEMA_VERSION,
        "python": platform.python_version(),
        "os": platform.system().lower(),
        "arch": platform.machine(),
    }


def usage_event(
    *,
    command: str,
    duration_ms: int,
    exit_code: int,
    report: Any = None,
    store: Any = None,
) -> dict[str, Any]:
    """One anonymous event: counts, versions and shape. No content.

    Reuses what ``IndexReport`` and the envelope already compute rather than
    measuring anything new, so enabling telemetry cannot change what a command
    does.
    """
    event: dict[str, Any] = {
        "event": "command",
        "command": command,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "install_id": install_id(),
        **_environment(),
    }

    if report is not None:
        event.update({
            "nodes": getattr(report, "nodes", 0),
            "edges": getattr(report, "edges", 0),
            "unresolved": getattr(report, "unresolved", 0),
            "boundaries": getattr(report, "boundaries", 0),
            "files_parsed": getattr(report, "files_parsed", 0),
            "files_skipped": getattr(report, "files_skipped", 0),
            # Language -> file count. A count is not identifying; a name is.
            "languages": dict(getattr(report, "languages", {}) or {}),
            "levels": dict(getattr(report, "levels", {}) or {}),
        })

    if store is not None:
        try:
            row = store.conn.execute(
                "SELECT grammar_digest, spec_digest, ontology_digest "
                "FROM graph_meta WHERE id = 1"
            ).fetchone()
            if row:
                event.update({
                    "grammar_digest": row["grammar_digest"],
                    "spec_digest": row["spec_digest"],
                    "ontology_digest": row["ontology_digest"],
                })
        except Exception:  # pragma: no cover - telemetry never fails a command
            logger.debug("could not read digests for telemetry", exc_info=True)

    return event


def assert_anonymous(event: dict[str, Any]) -> None:
    """Fail loudly if an identifying field reached an anonymous event.

    Walks keys rather than scanning serialised text, for the same reason
    ``upload.assert_no_source`` does: a docstring or a language name may
    legitimately contain the word "path", and a check that cannot tell a key
    from a value gets switched off the first time it cries wolf.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            found.update(set(FORBIDDEN_OSS_KEYS) & value.keys())
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(event)
    if found:
        raise ValueError(
            f"anonymous telemetry event contains identifying fields "
            f"{sorted(found)}"
        )


@dataclass(slots=True)
class ToolCall:
    """One metered MCP call. The unit of cost attribution."""

    tool: str
    duration_ms: int
    response_bytes: int
    intent: str | None = None
    result_count: int = 0
    truncated: bool = False

    @property
    def estimated_tokens(self) -> int:
        """Serialised size over a documented constant.

        Named an estimate because it is one: this process does not tokenise and
        should not pretend to. Billing on an estimate would be indefensible;
        reporting on one is fine when it says so.
        """
        return max(1, self.response_bytes // BYTES_PER_TOKEN)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": "mcp_call",
            "tool": self.tool,
            "duration_ms": self.duration_ms,
            "response_bytes": self.response_bytes,
            "estimated_tokens": self.estimated_tokens,
            "intent": self.intent,
            "result_count": self.result_count,
            "truncated": self.truncated,
        }


@dataclass
class Meter:
    """Buffers events and flushes once, at exit.

    In-memory and counter-only on the hot path: metering a call must not
    perform I/O per call, or the meter becomes the cost it is measuring.
    """

    events: list[dict[str, Any]] = field(default_factory=list)
    #: Set when a session is authenticated. Only these events carry identity,
    #: and only to the environment logged into.
    attribution: dict[str, Any] = field(default_factory=dict)
    dropped: int = 0

    def record(self, event: dict[str, Any]) -> None:
        if len(self.events) >= MAX_BUFFERED_EVENTS:
            # Bounded rather than unbounded: a long-running MCP session must
            # not grow memory forever. The count is reported so the gap is
            # visible instead of silent.
            self.dropped += 1
            return
        self.events.append(event)

    def record_tool_call(self, call: ToolCall) -> None:
        self.record(call.as_dict())

    def totals(self) -> dict[str, Any]:
        """What this session spent, for `telemetry status` and a final line."""
        calls = [e for e in self.events if e.get("event") == "mcp_call"]
        by_tool: dict[str, dict[str, int]] = {}
        for call in calls:
            entry = by_tool.setdefault(
                call["tool"], {"calls": 0, "estimated_tokens": 0, "duration_ms": 0}
            )
            entry["calls"] += 1
            entry["estimated_tokens"] += call.get("estimated_tokens", 0)
            entry["duration_ms"] += call.get("duration_ms", 0)
        return {
            "calls": len(calls),
            "estimated_tokens": sum(
                c.get("estimated_tokens", 0) for c in calls
            ),
            "by_tool": dict(sorted(by_tool.items())),
            "dropped": self.dropped,
        }

    def flush(self) -> bool:
        """Send buffered events. Returns whether anything was sent.

        Swallows every failure. A telemetry endpoint being down, slow or
        wrong must be invisible to the command that triggered it.
        """
        if not self.events:
            return False
        if not (is_enabled() or self.attribution):
            self.events.clear()
            return False

        payload = {
            "events": [dict(e) for e in self.events],
            "totals": self.totals(),
        }
        if self.attribution:
            # Identity is added only for an authenticated session, and goes
            # only to the environment that session logged into.
            payload["attribution"] = dict(self.attribution)
        else:
            for event in payload["events"]:
                try:
                    assert_anonymous(event)
                except ValueError:
                    logger.debug("dropping non-anonymous event", exc_info=True)
                    return False

        self.events.clear()
        try:
            return _post(payload)
        except Exception:  # pragma: no cover - by design
            logger.debug("telemetry flush failed", exc_info=True)
            return False


def _post(payload: dict[str, Any]) -> bool:
    import httpx

    from .auth_flow import ENVIRONMENTS
    from .credentials import CredentialsStore

    store = CredentialsStore()
    env = store.active_env
    stored = store.get(env) or {}
    base_url = stored.get("base_url") or ENVIRONMENTS.get(env, "")
    if not base_url:
        return False

    headers = {"Content-Type": "application/json"}
    token = stored.get("access_token")
    path = OSS_PATH
    if token and payload.get("attribution"):
        headers["X-API-Key"] = token
        path = METERING_PATH

    with httpx.Client(timeout=FLUSH_TIMEOUT_SECONDS) as client:
        response = client.post(base_url.rstrip("/") + path, json=payload,
                               headers=headers)
    return response.status_code < 400


#: Process-wide meter. One per process because the flush is at exit.
_METER: Meter | None = None


def meter() -> Meter:
    global _METER
    if _METER is None:
        _METER = Meter()
    return _METER


def reset_meter() -> None:
    """For tests. A module-level singleton would otherwise leak across them."""
    global _METER
    _METER = None


class timed:  # a context manager, named as a verb on purpose
    """Measure a block in milliseconds without caring whether it succeeded."""

    def __init__(self) -> None:
        self.ms = 0
        self._start = 0.0

    def __enter__(self) -> timed:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._start) * 1000)


__all__ = [
    "BYTES_PER_TOKEN",
    "FORBIDDEN_OSS_KEYS",
    "KILL_SWITCHES",
    "METERING_PATH",
    "OSS_PATH",
    "Meter",
    "ToolCall",
    "assert_anonymous",
    "install_id",
    "is_enabled",
    "meter",
    "prefs_path",
    "reset_meter",
    "set_enabled",
    "status",
    "suppressed_by",
    "timed",
    "usage_event",
]
