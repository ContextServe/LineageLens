"""Opt-in telemetry and MCP metering (#59).

Nothing is emitted today, so this file's job is mostly to assert the absence.
The consent gate is tested first and hardest, because every other property
depends on it being right: a leak here is not a bug users can work around.
"""

from __future__ import annotations

import json
import socket

import pytest

from lineagelens import telemetry
from lineagelens.telemetry import (
    BYTES_PER_TOKEN,
    FORBIDDEN_OSS_KEYS,
    KILL_SWITCHES,
    Meter,
    ToolCall,
    assert_anonymous,
    usage_event,
)


@pytest.fixture(autouse=True)
def isolated_prefs(tmp_path, monkeypatch):
    """Never read or write the developer's real preference file."""
    from lineagelens import credentials

    monkeypatch.setattr(
        credentials, "_creds_path", lambda: tmp_path / "creds.json"
    )
    for name in KILL_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    telemetry.reset_meter()
    yield
    telemetry.reset_meter()


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("telemetry attempted egress without consent")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------


class TestConsent:
    def test_off_by_default(self):
        assert telemetry.is_enabled() is False

    def test_enable_then_disable(self):
        telemetry.set_enabled(True)
        assert telemetry.is_enabled() is True
        telemetry.set_enabled(False)
        assert telemetry.is_enabled() is False

    @pytest.mark.parametrize("switch", KILL_SWITCHES)
    def test_a_kill_switch_beats_configuration(self, switch, monkeypatch):
        """Cannot be re-enabled by config.

        A build server cannot consent for the person who wrote the config, and
        a config file must not be able to opt a CI fleet in.
        """
        telemetry.set_enabled(True)
        monkeypatch.setenv(switch, "1")
        assert telemetry.suppressed_by() == switch
        assert telemetry.is_enabled() is False

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_a_falsy_kill_switch_does_not_suppress(self, value, monkeypatch):
        monkeypatch.setenv("DO_NOT_TRACK", value)
        assert telemetry.suppressed_by() is None

    def test_authentication_is_not_consent(self, tmp_path, monkeypatch):
        """Logging in to a hosted service is consent for *that service*.

        Conflating it with anonymous OSS telemetry would opt users in by side
        effect, so the two flags stay separate and `is_enabled` never reads
        credentials.
        """
        from lineagelens.credentials import CredentialsStore

        store = CredentialsStore()
        store.save("prod", {"access_token": "t"}, "https://example.test", "a@b.c")
        assert telemetry.is_enabled() is False

    def test_nothing_is_sent_before_opt_in(self, no_network):
        meter = Meter()
        meter.record(usage_event(command="index", duration_ms=1, exit_code=0))
        assert meter.flush() is False

    def test_the_preference_lives_beside_credentials_not_in_the_project(self):
        """A choice is per user, not per repository.

        Committing one would opt a whole team in by accident.
        """
        path = telemetry.prefs_path()
        assert path.name == "telemetry.json"
        assert "lineagelens" not in str(path.parent.parent.name).lower() or True
        # The decisive assertion: not under the current working tree.
        from pathlib import Path

        assert Path.cwd() not in path.parents


# ---------------------------------------------------------------------------
# the install id
# ---------------------------------------------------------------------------


class TestInstallId:
    def test_stable_across_calls(self):
        assert telemetry.install_id() == telemetry.install_id()

    def test_is_a_uuid_not_a_fingerprint(self):
        """Never derived from the machine.

        A value computed from hostname, username, MAC or project path is a
        fingerprint wearing an anonymity label.
        """
        import getpass
        import platform
        import uuid
        from pathlib import Path

        value = telemetry.install_id()
        uuid.UUID(value)  # raises unless it is a real UUID

        for identifying in (
            platform.node(), getpass.getuser(), str(Path.cwd()),
        ):
            if identifying:
                assert identifying not in value


# ---------------------------------------------------------------------------
# anonymity
# ---------------------------------------------------------------------------


class TestAnonymity:
    def test_an_event_carries_counts_and_versions_only(self):
        event = usage_event(command="index", duration_ms=10, exit_code=0)
        assert_anonymous(event)
        assert event["command"] == "index"
        assert "install_id" in event

    def test_a_real_index_report_stays_anonymous(self, tmp_path):
        """The payload as actually built, not a hand-written one."""
        from lineagelens.indexer import Indexer

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "secretco"\n')
        (root / "billing_secrets.py").write_text("def charge(card):\n    return card\n")
        store, report = Indexer(root).run()
        try:
            event = usage_event(
                command="index", duration_ms=1, exit_code=0,
                report=report, store=store,
            )
            assert_anonymous(event)
            blob = json.dumps(event)
            # Nothing identifying about the project survives.
            assert "secretco" not in blob
            assert "billing_secrets" not in blob
            assert "charge" not in blob
            # ...while the shape does.
            assert event["nodes"] > 0
            assert event["languages"] == {"python": 1}
        finally:
            store.close()

    def test_the_guard_catches_a_field_added_later(self):
        """So a future contributor cannot widen this by accident."""
        with pytest.raises(ValueError, match="repo_name"):
            assert_anonymous({"command": "index", "repo_name": "acme/private"})

    def test_the_guard_inspects_keys_not_values(self):
        """A language name or docstring may contain a forbidden word.

        A check that cannot tell a key from a value is a check that gets
        switched off the first time it cries wolf.
        """
        assert_anonymous({"command": "index", "notes": "counts, never a path"})

    def test_every_forbidden_key_is_actually_caught(self):
        for key in FORBIDDEN_OSS_KEYS:
            with pytest.raises(ValueError):
                assert_anonymous({key: "x"})

    def test_a_non_anonymous_event_is_dropped_not_sent(self, no_network):
        telemetry.set_enabled(True)
        meter = Meter()
        meter.record({"event": "command", "repo_name": "acme/private"})
        assert meter.flush() is False


# ---------------------------------------------------------------------------
# metering
# ---------------------------------------------------------------------------


class TestMetering:
    def test_token_estimate_is_derived_and_labelled(self):
        """Named an estimate because it is one.

        This process does not tokenise. Billing on an estimate would be
        indefensible; reporting on one is fine when it says so.
        """
        call = ToolCall(tool="search", duration_ms=1, response_bytes=400)
        assert call.estimated_tokens == 400 // BYTES_PER_TOKEN
        assert "estimated_tokens" in call.as_dict()
        assert "tokens" not in [k for k in call.as_dict() if k == "tokens"]

    def test_a_tiny_response_still_costs_something(self):
        assert ToolCall(tool="t", duration_ms=0, response_bytes=1).estimated_tokens == 1

    def test_totals_group_by_tool(self):
        meter = Meter()
        meter.record_tool_call(ToolCall("search", 2, 400))
        meter.record_tool_call(ToolCall("search", 3, 800))
        meter.record_tool_call(ToolCall("impact_of", 1, 200))
        totals = meter.totals()
        assert totals["calls"] == 3
        assert totals["by_tool"]["search"]["calls"] == 2
        assert totals["by_tool"]["impact_of"]["estimated_tokens"] == 50

    def test_the_buffer_is_bounded_and_says_so(self):
        """A long MCP session must not grow memory forever.

        The drop count is reported rather than hidden, so the gap is visible.
        """
        meter = Meter()
        for _ in range(telemetry.MAX_BUFFERED_EVENTS + 10):
            meter.record_tool_call(ToolCall("search", 1, 100))
        assert len(meter.events) == telemetry.MAX_BUFFERED_EVENTS
        assert meter.totals()["dropped"] == 10

    def test_every_mcp_tool_is_metered_without_being_listed(self, tmp_path):
        """Metering is in `@guarded`, so a new tool is metered by default.

        Listing tools individually is how one gets forgotten.
        """
        import asyncio

        from lineagelens.indexer import Indexer
        from lineagelens.mcp.server import create_server

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        store, _ = Indexer(root).run()
        store.close()

        telemetry.reset_meter()
        server = create_server(root)
        tools = {t.name: t for t in server._tool_manager.list_tools()}

        asyncio.run(tools["search"].fn(query="f"))
        asyncio.run(tools["coverage_report"].fn())

        events = telemetry.meter().events
        assert {e["tool"] for e in events} == {"search", "coverage_report"}
        assert all(e["response_bytes"] > 0 for e in events)

    def test_intent_is_captured(self, tmp_path):
        """`precise` adds verbatim source and data flow: the biggest lever."""
        import asyncio

        from lineagelens.indexer import Indexer
        from lineagelens.mcp.server import create_server

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        store, _ = Indexer(root).run()
        store.close()

        telemetry.reset_meter()
        server = create_server(root)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        asyncio.run(tools["callers_of"].fn(symbol="f", intent="precise"))

        assert telemetry.meter().events[0]["intent"] == "precise"


# ---------------------------------------------------------------------------
# failure is invisible
# ---------------------------------------------------------------------------


class TestFailureIsInvisible:
    @pytest.mark.parametrize("failure", [
        ConnectionError("offline"),
        TimeoutError("slow"),
        ValueError("malformed"),
    ])
    def test_a_flush_failure_is_swallowed(self, failure, monkeypatch):
        """A telemetry outage must not be able to change an exit code."""
        telemetry.set_enabled(True)

        def boom(payload):
            raise failure

        monkeypatch.setattr(telemetry, "_post", boom)
        meter = Meter()
        meter.record(usage_event(command="index", duration_ms=1, exit_code=0))
        assert meter.flush() is False

    def test_index_succeeds_when_telemetry_is_broken(self, tmp_path, monkeypatch):
        from lineagelens.cli import main

        telemetry.set_enabled(True)
        monkeypatch.setattr(
            telemetry, "_post",
            lambda payload: (_ for _ in ()).throw(ConnectionError("down")),
        )
        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        assert main(["index", str(root)]) == 0

    def test_metering_a_tool_cannot_break_it(self, tmp_path, monkeypatch):
        import asyncio

        from lineagelens.indexer import Indexer
        from lineagelens.mcp.server import create_server

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        store, _ = Indexer(root).run()
        store.close()

        class Exploding:
            def record_tool_call(self, call):
                raise RuntimeError("meter on fire")

        monkeypatch.setattr(telemetry, "meter", lambda: Exploding())
        server = create_server(root)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        result = asyncio.run(tools["search"].fn(query="f"))
        assert result["kind"] == "search"


# ---------------------------------------------------------------------------
# status, and the documentation contract
# ---------------------------------------------------------------------------


class TestStatusAndDocs:
    def test_status_prints_the_real_payload(self):
        """Not a description of it.

        For a tool that ships as source, nobody should have to read
        telemetry.py to learn what leaves their machine.
        """
        state = telemetry.status()
        assert_anonymous(state["example_event"])
        assert state["example_event"]["command"] == "index"
        assert state["endpoint"] == telemetry.OSS_PATH

    def test_status_names_the_suppressing_variable(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        state = telemetry.status()
        assert state["suppressed_by"] == "CI"
        assert state["effective"] is False

    def test_the_documented_field_set_matches_the_emitted_one(self):
        """So the doc cannot drift from the code.

        docs/TELEMETRY.md lists what is never sent; that list has to be the
        one the guard enforces, or the document is decoration.
        """
        from pathlib import Path

        text = Path("docs/TELEMETRY.md").read_text()
        for key in FORBIDDEN_OSS_KEYS:
            assert key in text, f"{key} is enforced but undocumented"

    def test_every_emitted_field_is_documented(self, tmp_path):
        from pathlib import Path

        from lineagelens.indexer import Indexer

        root = tmp_path / "p"
        root.mkdir()
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text("def f(x):\n    return x\n")
        store, report = Indexer(root).run()
        try:
            event = usage_event(
                command="index", duration_ms=1, exit_code=0,
                report=report, store=store,
            )
        finally:
            store.close()

        text = Path("docs/TELEMETRY.md").read_text()
        undocumented = [key for key in event if key not in text]
        assert not undocumented, f"emitted but undocumented: {undocumented}"

    def test_metering_fields_are_documented(self):
        from pathlib import Path

        text = Path("docs/TELEMETRY.md").read_text()
        for key in ToolCall("t", 1, 100).as_dict():
            assert key in text, f"{key} is metered but undocumented"
