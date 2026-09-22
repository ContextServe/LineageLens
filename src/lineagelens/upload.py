"""Publishing a graph to ContextServe (#58).

Replaces the removed ``analyze --upload``. The graph is built locally and
published over the existing credential flow; there is no ``--key`` flag,
because the key is a header and the CLI already has a login.

Three rules shape everything here.

**Send the graph, not the source.** Nodes carry a signature and a docstring, and
those go. ``usage_sites`` holds ``context_before`` / ``context_line`` /
``context_after`` -- verbatim source lines -- and those are excluded. Uploading
source into a context product is a materially different privacy proposition from
uploading its structure, and the difference has to be visible in the code rather
than merely intended. :func:`assert_no_source` is that visibility, and a test
asserts it over the serialised payload.

**Publication is a side effect; the graph is the product.** A failed upload
prints a diagnostic and leaves the exit code alone when the index itself
succeeded. A billing limit, an expired token or an outage must not break
someone's build.

**Nothing is gated here.** Tier enforcement is the server deciding whether to
accept a payload. This module has no notion of a plan.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .core import NodeFlags
from .store import GraphStore

logger = logging.getLogger(__name__)

#: Ingest route on ContextServe. Authenticated with ``X-API-Key``.
UPLOAD_PATH = "/api/v1/graphs/upload"

#: Environment variable holding a token, for CI. Browser OTP cannot work in a
#: headless job, and CI is the main place ``--upload`` belongs -- so there is
#: one auth mechanism and exactly one headless escape hatch.
TOKEN_ENV = "LINEAGELENS_TOKEN"  # noqa: S105 - a variable name, not a secret

#: Compress above this size. Below it the round trip costs more than it saves.
GZIP_THRESHOLD_BYTES = 64 * 1024

#: Columns that hold verbatim source text and must never leave the machine.
#: Named rather than implied, so a reviewer can check the list against the
#: schema and a test can assert none of them appear in a payload.
SOURCE_COLUMNS = ("context_before", "context_line", "context_after")
SOURCE_COLUMNS_SET = frozenset(SOURCE_COLUMNS)


class UploadError(RuntimeError):
    """Publication failed. Never fatal when the index succeeded."""


class AuthMissing(UploadError):
    """No credential resolved. Carries the remedy, not an HTTP status."""


@dataclass(slots=True)
class UploadResult:
    ok: bool
    status: str = ""
    repo_name: str = ""
    nodes: int = 0
    edges: int = 0
    digest: str = ""
    destination: str = ""
    detail: str = ""
    #: True when the server already had this digest, so nothing was stored.
    unchanged: bool = False


@dataclass(slots=True)
class Credential:
    token: str
    base_url: str
    env: str
    source: str  # flag | env | stored -- printed so a user knows which won


def resolve_credential(
    env: str | None = None, token: str | None = None
) -> Credential:
    """Find a token. Explicit flag, then environment, then stored login.

    Ordered most-explicit-first so a one-off run can override a stored default
    without switching it, which is why ``--env`` exists as well.
    """
    from .auth_flow import ENVIRONMENTS
    from .credentials import CredentialsStore

    store = CredentialsStore()
    target = env or store.active_env

    if token:
        return Credential(token, ENVIRONMENTS.get(target, ""), target, "flag")

    from_env = os.environ.get(TOKEN_ENV)
    if from_env:
        return Credential(from_env, ENVIRONMENTS.get(target, ""), target, "env")

    stored = store.get(target)
    if stored and stored.get("access_token"):
        return Credential(
            stored["access_token"],
            stored.get("base_url") or ENVIRONMENTS.get(target, ""),
            target,
            "stored",
        )

    raise AuthMissing(
        f"no credential for environment {target!r}.\n"
        f"  run:  lineagelens auth login --env {target}\n"
        f"  or:   export {TOKEN_ENV}=<token>   (for CI)"
    )


def build_payload(
    store: GraphStore, *, repo_name: str, report: Any = None
) -> dict[str, Any]:
    """Project the stored graph into the ingest payload.

    Built from the tables rather than from an in-memory report, so what is sent
    is what was persisted. A report is a summary of a build; the graph is the
    thing being published.
    """
    conn = store.conn
    meta = conn.execute(
        "SELECT schema_version, build_digest, grammar_digest, spec_digest, "
        "       adapter_digest, ontology_digest, built_at, commit_sha, branch, "
        "       dirty FROM graph_meta WHERE id = 1"
    ).fetchone()

    nodes = [
        {
            "id": row["id"],
            "qualified_name": row["qualified_name"],
            "name": row["name"],
            "kind": row["kind"],
            "lang": row["lang"],
            "service": row["service_id"],
            "file": row["file_path"],
            "line": row["start_line"],
            "end_line": row["end_line"],
            "signature": row["signature"],
            "docstring": row["docstring"],
            "visibility": row["visibility"],
            # Names, not the integer: a consumer should not have to know that
            # ENTRY_POINT is 256.
            "flags": [
                f.name for f in NodeFlags if f.value and (row["flags"] & f.value)
            ],
            "parent": row["parent_id"],
        }
        for row in conn.execute(
            "SELECT id, qualified_name, name, kind, lang, service_id, file_path, "
            "       start_line, end_line, signature, docstring, visibility, "
            "       flags, parent_id FROM nodes ORDER BY id"
        )
    ]

    edges = [
        {
            "src": row["src"],
            "dst": row["dst"],
            "kind": row["kind"],
            "file": row["file_path"],
            "line": row["line"],
            # The evidence tier is what makes a negative answer trustworthy.
            # Dropping it to save bytes would make the uploaded graph strictly
            # less honest than the local one.
            "evidence_tier": row["evidence_tier"],
            "evidence_label": row["evidence_label"],
            "resolution": row["resolution"],
        }
        for row in conn.execute(
            "SELECT src, dst, kind, file_path, line, evidence_tier, "
            "       evidence_label, resolution FROM edges "
            "ORDER BY src, dst, kind, IFNULL(start_byte, -1)"
        )
    ]

    services = [
        {"id": row["id"], "name": row["name"], "root": row["root"],
         "kind": row["kind"], "manifest": row["manifest_path"]}
        for row in conn.execute(
            "SELECT id, name, root, kind, manifest_path FROM services ORDER BY id"
        )
    ]

    unresolved = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status, count(*) AS n FROM unresolved_refs GROUP BY status"
        )
    }

    files = {
        row["parse_status"]: row["n"]
        for row in conn.execute(
            "SELECT parse_status, count(*) AS n FROM files GROUP BY parse_status"
        )
    }

    payload: dict[str, Any] = {
        "repo_name": repo_name,
        "graph_data": {
            "graph_meta": {
                "schema_version": meta["schema_version"],
                "build_digest": meta["build_digest"],
                "grammar_digest": meta["grammar_digest"],
                "spec_digest": meta["spec_digest"],
                "adapter_digest": meta["adapter_digest"],
                "ontology_digest": meta["ontology_digest"],
                "built_at": meta["built_at"],
            },
            "repo": {
                "commit_sha": meta["commit_sha"],
                "branch": meta["branch"],
                "dirty": None if meta["dirty"] is None else bool(meta["dirty"]),
            },
            "nodes": nodes,
            "edges": edges,
            "services": services,
            "coverage": {
                "files": files,
                "unresolved": unresolved,
                # Frameworks present but unmodelled. Without it an empty
                # contract map is indistinguishable from "these services are
                # genuinely not connected".
                "unclaimed_frameworks": store.unclaimed_frameworks(),
            },
        },
    }

    if report is not None:
        levels = getattr(report, "levels", None)
        if levels:
            payload["graph_data"]["capability"] = {"levels": dict(levels)}

    return payload


def assert_no_source(payload: dict[str, Any]) -> None:
    """Fail loudly if verbatim source text reached the payload.

    Walks every key in the structure rather than auditing the fields this
    module happens to write, so it still catches a column added later by
    someone who did not read this docstring. Raising is correct: silently
    stripping would make the guarantee depend on this function being called,
    and an assertion that can be skipped is not a guarantee.

    Keys, not serialised text. The first version scanned ``json.dumps(payload)``
    for the column names and fired on LineageLens' own index -- a *docstring*
    in ``store/db.py`` mentions ``context_before``, and a docstring is
    legitimate payload. A check that cannot tell a key from a value would
    block any repository that discusses source extraction, which is a
    false positive severe enough to get the check disabled.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            found.update(SOURCE_COLUMNS_SET & value.keys())
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    if found:
        raise UploadError(
            f"refusing to upload: payload contains source text columns "
            f"{sorted(found)}. The graph is structure, not contents."
        )


def serialise(payload: dict[str, Any]) -> tuple[bytes, bool]:
    """JSON bytes, gzipped when worth it. Returns ``(body, compressed)``."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    if len(raw) < GZIP_THRESHOLD_BYTES:
        return raw, False
    # mtime=0 so the same graph produces the same bytes. A timestamp in the
    # gzip header would make an idempotent upload look different on the wire.
    return gzip.compress(raw, mtime=0), True


def upload(
    store: GraphStore,
    *,
    repo_name: str,
    credential: Credential,
    report: Any = None,
    timeout: float = 60.0,
) -> UploadResult:
    """Publish one graph. Raises :class:`UploadError` on any failure."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - extra is declared
        raise UploadError(
            "upload needs the auth extra:\n  pip install 'lineagelens[auth]'"
        ) from exc

    payload = build_payload(store, repo_name=repo_name, report=report)
    assert_no_source(payload)
    body, compressed = serialise(payload)

    graph = payload["graph_data"]
    digest = graph["graph_meta"]["build_digest"] or ""
    headers = {
        "Content-Type": "application/json",
        # Sent so the server can short-circuit before reading the body, once it
        # supports that. Harmless if ignored.
        "X-Build-Digest": digest,
    }
    
    if credential.token.startswith("ll_live_"):
        headers["X-API-Key"] = credential.token
    else:
        headers["Authorization"] = f"Bearer {credential.token}"
        
    if compressed:
        headers["Content-Encoding"] = "gzip"

    url = credential.base_url.rstrip("/") + UPLOAD_PATH
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, content=body, headers=headers)
    except Exception as exc:
        raise UploadError(f"could not reach {url}: {exc}") from exc

    if response.status_code == 401:
        raise UploadError(
            f"rejected: token for {credential.env!r} is invalid or expired.\n"
            f"  run:  lineagelens auth login --env {credential.env}"
        )
    if response.status_code == 402:
        raise UploadError(f"plan limit reached: {_detail(response)}")
    if response.status_code == 403:
        raise UploadError(f"not permitted: {_detail(response)}")
    if response.status_code == 413:
        raise UploadError(
            f"graph too large for this plan or server: {_detail(response)}"
        )
    if response.status_code >= 400:
        raise UploadError(f"HTTP {response.status_code}: {_detail(response)}")

    data = _json_or_empty(response)
    status = str(data.get("status", "success"))
    return UploadResult(
        ok=True,
        status=status,
        unchanged=status == "unchanged",
        repo_name=str(data.get("repo_name", repo_name)),
        nodes=len(graph["nodes"]),
        edges=len(graph["edges"]),
        digest=digest,
        destination=credential.base_url,
    )


def _json_or_empty(response: Any) -> dict[str, Any]:
    try:
        parsed = response.json()
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _detail(response: Any) -> str:
    """A server message a user can act on, never a bare status code."""
    data = _json_or_empty(response)
    detail = data.get("detail", data)
    if isinstance(detail, dict):
        return detail.get("reason") or detail.get("error") or json.dumps(detail)
    return str(detail)[:400] or f"HTTP {response.status_code}"


__all__ = [
    "GZIP_THRESHOLD_BYTES",
    "SOURCE_COLUMNS",
    "TOKEN_ENV",
    "UPLOAD_PATH",
    "AuthMissing",
    "Credential",
    "UploadError",
    "UploadResult",
    "assert_no_source",
    "build_payload",
    "resolve_credential",
    "serialise",
    "upload",
]
