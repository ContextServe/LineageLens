"""Publishing a graph (#58).

Replaces the removed ``analyze --upload``. Three properties carry the design,
and each has a test that fails loudly rather than a comment that hopes:

* **a plain ``index`` makes no network calls.** Asserted by failing on any
  outbound socket, not by inspecting code;
* **the payload carries structure, not source.** ``usage_sites`` holds verbatim
  lines and they are excluded;
* **publication failing is not indexing failing.** The graph is the product.
"""

from __future__ import annotations

import json
import socket

import pytest

from lineagelens.indexer import Indexer
from lineagelens.upload import (
    SOURCE_COLUMNS,
    TOKEN_ENV,
    Credential,
    UploadError,
    assert_no_source,
    build_payload,
    resolve_credential,
    serialise,
)

PROJECT = {
    "pyproject.toml": '[project]\nname = "shop"\n',
    "app/repo.py": "class Repository:\n    def save(self, amount):\n        return amount\n",
    "app/routes.py": (
        "from app.repo import Repository\n\n"
        "@router.post('/api/orders')\n"
        "def create_order(repo: Repository, amount):\n"
        "    return repo.save(amount)\n"
    ),
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "shop"
    for rel, text in PROJECT.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def indexed(project):
    store, report = Indexer(project).run()
    yield store, report, project
    store.close()


@pytest.fixture
def no_network(monkeypatch):
    """Fail the test on any outbound connection.

    Patching the socket rather than trusting a code read: the guarantee is
    about behaviour, and a future import could reintroduce egress without
    touching anything a reviewer would think to look at.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("outbound network call during an offline operation")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# ---------------------------------------------------------------------------
# offline by default
# ---------------------------------------------------------------------------


class TestOptInEgress:
    def test_a_plain_index_makes_no_network_calls(self, project, no_network):
        """The property that makes the tool safe to run on private code."""
        store, report = Indexer(project).run()
        try:
            assert report.nodes > 0
        finally:
            store.close()

    def test_query_and_coverage_make_no_network_calls(self, project, no_network):
        from lineagelens.query import QueryEngine

        store, _ = Indexer(project).run()
        try:
            engine = QueryEngine(store, str(project))
            assert engine.search("save").returned >= 0
            assert engine.coverage_report().kind
        finally:
            store.close()

    def test_building_a_payload_makes_no_network_calls(self, indexed, no_network):
        """Assembling and auditing a payload is local; only sending is not."""
        store, report, _ = indexed
        payload = build_payload(store, repo_name="shop", report=report)
        assert_no_source(payload)
        serialise(payload)


# ---------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------


class TestPayload:
    def test_carries_the_graph(self, indexed):
        store, report, _ = indexed
        graph = build_payload(store, repo_name="shop", report=report)["graph_data"]
        assert graph["nodes"] and graph["edges"]
        assert graph["graph_meta"]["build_digest"]
        assert graph["graph_meta"]["schema_version"]

    def test_flags_are_names_not_a_bitfield(self, indexed):
        """A consumer should not have to know that ENTRY_POINT is 256."""
        store, report, _ = indexed
        graph = build_payload(store, repo_name="shop", report=report)["graph_data"]
        flagged = [n for n in graph["nodes"] if n["flags"]]
        assert flagged, "fixture produced no flagged node"
        assert all(isinstance(f, str) for n in flagged for f in n["flags"])

    def test_entry_points_are_discoverable_from_the_payload(self, indexed):
        """What the server counts (#57, ContextServeWebsite#4).

        The EXPOSES sources and the ENTRY_POINT-flagged nodes must be the same
        set, so a server may use either and get the same answer.
        """
        store, report, _ = indexed
        graph = build_payload(store, repo_name="shop", report=report)["graph_data"]
        exposing = {
            e["src"] for e in graph["edges"] if e["kind"] == "EXPOSES"
        }
        flagged = {
            n["id"] for n in graph["nodes"] if "ENTRY_POINT" in n["flags"]
        }
        assert exposing, "fixture exposes no contract"
        assert exposing == flagged

    def test_edges_keep_their_evidence_tier(self, indexed):
        """Dropping it would make the uploaded graph less honest than the local one."""
        store, report, _ = indexed
        graph = build_payload(store, repo_name="shop", report=report)["graph_data"]
        assert all("evidence_tier" in e for e in graph["edges"])

    def test_carries_provenance_and_capability(self, indexed):
        store, report, _ = indexed
        graph = build_payload(store, repo_name="shop", report=report)["graph_data"]
        assert set(graph["repo"]) == {"commit_sha", "branch", "dirty"}
        assert graph["capability"]["levels"]

    def test_carries_the_coverage_envelope(self, indexed):
        """Without it an empty answer is indistinguishable from an unread one."""
        store, report, _ = indexed
        graph = build_payload(store, repo_name="shop", report=report)["graph_data"]
        assert "files" in graph["coverage"]
        assert "unresolved" in graph["coverage"]
        assert "unclaimed_frameworks" in graph["coverage"]


# ---------------------------------------------------------------------------
# source text never leaves
# ---------------------------------------------------------------------------


class TestNoSourceLeaves:
    def test_no_usage_site_context_in_the_payload(self, indexed):
        store, report, _ = indexed
        payload = build_payload(store, repo_name="shop", report=report)
        blob = json.dumps(payload)
        for column in SOURCE_COLUMNS:
            assert f'"{column}":' not in blob

    def test_the_guard_catches_a_column_added_later(self):
        """Walks keys, so it does not depend on this module's field list."""
        with pytest.raises(UploadError, match="context_line"):
            assert_no_source({"graph_data": {"nodes": [{"context_line": "x = 1"}]}})

    def test_the_guard_inspects_keys_not_values(self):
        """A docstring may legitimately mention a column name.

        The first version scanned the serialised JSON and fired on
        LineageLens' own index, because a docstring in `store/db.py` discusses
        `context_before`. A check that cannot tell a key from a value would
        block any repository that talks about source extraction -- a false
        positive severe enough to get the check switched off.
        """
        assert_no_source({
            "graph_data": {
                "nodes": [{
                    "docstring": "Stores context_before and context_after.",
                    "signature": "(context_line: str) -> None",
                }],
            },
        })

    def test_docstrings_and_signatures_do_go(self):
        """Structure includes the interface. Only file contents are withheld."""
        store, report = None, None
        import tempfile
        from pathlib import Path

        from lineagelens.indexer import Indexer as I

        root = Path(tempfile.mkdtemp()) / "p"
        root.mkdir(parents=True)
        (root / "pyproject.toml").write_text('[project]\nname = "p"\n')
        (root / "a.py").write_text(
            'def f(x: int) -> int:\n    """Doubles x."""\n    return x * 2\n'
        )
        store, report = I(root).run()
        try:
            graph = build_payload(store, repo_name="p", report=report)["graph_data"]
            f = next(n for n in graph["nodes"] if n["name"] == "f")
            assert f["docstring"] and "Doubles" in f["docstring"]
            assert f["signature"]
        finally:
            store.close()


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


class TestSerialise:
    def test_small_payloads_are_not_compressed(self):
        body, compressed = serialise({"a": 1})
        assert compressed is False
        assert json.loads(body) == {"a": 1}

    def test_large_payloads_are_compressed(self):
        import gzip

        payload = {"graph_data": {"nodes": [{"id": f"n{i}"} for i in range(20000)]}}
        body, compressed = serialise(payload)
        assert compressed is True
        assert json.loads(gzip.decompress(body)) == payload

    def test_compression_is_byte_stable(self):
        """A gzip timestamp would make an idempotent upload differ on the wire."""
        payload = {"graph_data": {"nodes": [{"id": f"n{i}"} for i in range(20000)]}}
        assert serialise(payload)[0] == serialise(payload)[0]


# ---------------------------------------------------------------------------
# auth resolution
# ---------------------------------------------------------------------------


class TestCredentialResolution:
    def test_explicit_token_wins(self, monkeypatch):
        monkeypatch.setenv(TOKEN_ENV, "from-env")
        credential = resolve_credential(env="prod", token="from-flag")  # noqa: S106
        assert credential.source == "flag"

    def test_environment_is_the_ci_path(self, monkeypatch):
        """Browser OTP cannot work headless, so there is exactly one escape hatch."""
        monkeypatch.setenv(TOKEN_ENV, "from-env")
        credential = resolve_credential(env="prod")
        assert credential.source == "env"
        assert credential.token == "from-env"  # noqa: S105

    def test_a_missing_credential_names_the_remedy(self, monkeypatch, tmp_path):
        """Not an HTTP status. The user needs the command to run."""
        from lineagelens import credentials

        monkeypatch.delenv(TOKEN_ENV, raising=False)
        monkeypatch.setattr(credentials, "_creds_path", lambda: tmp_path / "none.json")
        with pytest.raises(UploadError, match="auth login"):
            resolve_credential(env="prod")

    def test_there_is_no_key_flag(self):
        """`--key=ll_live_…` was the old shape; the key is a header now."""
        from lineagelens.cli import main

        with pytest.raises(SystemExit):
            main(["index", ".", "--key=ll_live_abc"])


# ---------------------------------------------------------------------------
# failure is never fatal
# ---------------------------------------------------------------------------


class TestFailureIsNotFatal:
    def _run(self, project, monkeypatch, capsys, extra=()):
        from lineagelens import upload as upload_mod
        from lineagelens.cli import main

        def boom(*args, **kwargs):
            raise upload_mod.UploadError("server exploded")

        monkeypatch.setattr(upload_mod, "upload", boom)
        monkeypatch.setenv(TOKEN_ENV, "t")
        code = main(["index", str(project), "--upload", *extra])
        return code, capsys.readouterr()

    def test_upload_failure_exits_zero(self, project, monkeypatch, capsys):
        """The graph is the product; publication is a side effect.

        An offline laptop, an expired token or an outage must not break
        someone's build.
        """
        code, out = self._run(project, monkeypatch, capsys)
        assert code == 0
        assert "upload failed" in out.err
        assert "server exploded" in out.err

    def test_upload_required_exits_non_zero(self, project, monkeypatch, capsys):
        code, _ = self._run(
            project, monkeypatch, capsys, extra=["--upload-required"]
        )
        assert code == 1

    def test_an_expired_token_prints_the_remedy(self):
        """A 401 must say what to run, not what the status was."""
        import httpx

        from lineagelens import upload as upload_mod

        class Fake:
            status_code = 401

            def json(self):
                return {"detail": "invalid"}

        transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"detail": "invalid"})
        )
        original = httpx.Client

        class Patched(original):
            def __init__(self, *a, **kw):
                kw["transport"] = transport
                super().__init__(*a, **kw)

        httpx.Client = Patched
        try:
            with pytest.raises(UploadError, match="auth login"):
                upload_mod.upload(
                    store=_StubStore(),
                    repo_name="p",
                    credential=Credential("t", "https://example.test", "prod", "env"),
                )
        finally:
            httpx.Client = original


class _StubStore:
    """Minimal store for exercising transport without building a graph."""

    def __init__(self) -> None:
        import sqlite3

        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            "CREATE TABLE graph_meta (id INTEGER, schema_version INT, "
            " build_digest TEXT, grammar_digest TEXT, spec_digest TEXT, "
            " adapter_digest TEXT, ontology_digest TEXT, built_at TEXT, "
            " commit_sha TEXT, branch TEXT, dirty INT);"
            "INSERT INTO graph_meta VALUES (1, 5, 'd', 'g', 's', 'a', 'o', 't',"
            " 'c', 'b', 0);"
            "CREATE TABLE nodes (id TEXT, qualified_name TEXT, name TEXT, "
            " kind TEXT, lang TEXT, service_id TEXT, file_path TEXT, "
            " start_line INT, end_line INT, signature TEXT, docstring TEXT, "
            " visibility TEXT, flags INT, parent_id TEXT);"
            "CREATE TABLE edges (src TEXT, dst TEXT, kind TEXT, file_path TEXT,"
            " line INT, evidence_tier TEXT, evidence_label TEXT, "
            " resolution TEXT, start_byte INT);"
            "CREATE TABLE services (id TEXT, name TEXT, root TEXT, kind TEXT,"
            " manifest_path TEXT);"
            "CREATE TABLE unresolved_refs (status TEXT);"
            "CREATE TABLE files (parse_status TEXT);"
        )

    def unclaimed_frameworks(self):
        return []


# ---------------------------------------------------------------------------
# end to end against a stub server
# ---------------------------------------------------------------------------


class TestAgainstAStubServer:
    """The real contract: POST /api/v1/graphs/upload with X-API-Key.

    A stub rather than a mocked client, so the headers, the encoding and the
    body all have to actually be right -- which is where a hand-rolled
    transport goes wrong.
    """

    @pytest.fixture
    def stub(self):
        import gzip as gziplib
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        received: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received["path"] = self.path
                received["api_key"] = self.headers.get("X-API-Key")
                received["encoding"] = self.headers.get("Content-Encoding")
                received["digest_header"] = self.headers.get("X-Build-Digest")
                if received["encoding"] == "gzip":
                    body = gziplib.decompress(body)
                received["payload"] = json.loads(body)
                out = json.dumps(
                    {"status": "success", "repo_name": "shop", "repo_id": "r1"}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}", received
        server.shutdown()

    def test_the_whole_round_trip(self, indexed, stub):
        from lineagelens.upload import upload

        base_url, received = stub
        store, report, _ = indexed
        result = upload(
            store,
            repo_name="shop",
            credential=Credential("ll_live_k", base_url, "local", "env"),
            report=report,
        )

        assert result.ok and result.status == "success"
        assert received["path"] == "/api/v1/graphs/upload"
        # A header, not a --key flag. That was the old shape.
        assert received["api_key"] == "ll_live_k"
        assert received["digest_header"] == result.digest

        graph = received["payload"]["graph_data"]
        assert len(graph["nodes"]) == result.nodes
        assert len(graph["edges"]) == result.edges
        assert graph["capability"]["levels"]
        assert any("ENTRY_POINT" in n["flags"] for n in graph["nodes"])

    def test_an_unchanged_digest_is_reported_as_unchanged(self, indexed, stub):
        """Idempotency is what makes --upload safe in CI."""
        import lineagelens.upload as upload_mod

        base_url, _ = stub
        store, report, _ = indexed

        class Unchanged:
            status_code = 200

            def json(self):
                return {"status": "unchanged", "repo_name": "shop"}

        import httpx

        original = httpx.Client.post
        httpx.Client.post = lambda self, *a, **kw: Unchanged()
        try:
            result = upload_mod.upload(
                store,
                repo_name="shop",
                credential=Credential("k", base_url, "local", "env"),
                report=report,
            )
        finally:
            httpx.Client.post = original
        assert result.unchanged is True
