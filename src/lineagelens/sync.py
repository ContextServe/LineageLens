"""ContextServe.ai Cloud Reporting & Synchronization (#mcp-metrics).

Syncs local SQLite metrics from .lineagelens/metrics.sqlite to ContextServe.ai.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .credentials import CredentialsStore
from .store.metrics_store import MetricsStore

logger = logging.getLogger(__name__)

DEFAULT_CLOUD_ENDPOINT = "https://contextserve.ai"
BATCH_SYNC_PATH = "/api/v1/telemetry/tokens/batch"
SINGLE_SYNC_PATH = "/api/v1/telemetry/tokens"


def get_auth_token_and_base_url(custom_endpoint: str | None = None) -> tuple[str | None, str, str | None]:
    """Retrieve auth token, base URL, and org from env or credentials store.

    Returns:
        (token, base_url, org)
    """
    token = os.environ.get("CONTEXTSERVE_API_KEY") or os.environ.get("LINEAGELENS_API_TOKEN")
    base_url = custom_endpoint or os.environ.get("CONTEXTSERVE_BASE_URL")
    org = None

    store = CredentialsStore()
    active_env = store.active_env
    creds = store.get(active_env) or {}

    if not token and creds:
        token = creds.get("access_token")
    if not base_url:
        base_url = creds.get("base_url") or DEFAULT_CLOUD_ENDPOINT
    if creds:
        org = creds.get("organization") or creds.get("org")

    return token, base_url.rstrip("/"), org


def sync_metrics(
    project_root: Path,
    endpoint: str | None = None,
    batch_size: int = 100,
) -> tuple[bool, str, int]:
    """Upload pending local invocation records to ContextServe.ai.

    Returns:
        (success, message, count_synced)
    """
    import httpx

    token, base_url, org = get_auth_token_and_base_url(endpoint)
    if not token:
        msg = (
            "Not authenticated to ContextServe.ai.\n"
            "Run `lineagelens auth login` or set `CONTEXTSERVE_API_KEY=<token>` to sync metrics."
        )
        return False, msg, 0

    store = MetricsStore.for_project(project_root)
    total_synced = 0

    headers = {"Content-Type": "application/json"}
    if token.startswith("ll_live_"):
        headers["X-API-Key"] = token
    else:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Auth-Token"] = f"Bearer {token}"

    while True:
        pending = store.get_unsynced(limit=batch_size)
        if not pending:
            break

        payload_events = []
        ids = []
        for r in pending:
            ids.append(r["id"])
            payload_events.append({
                "query_type": r["query_type"],
                "raw_tokens": r["raw_tokens"],
                "optimized_tokens": r["optimized_tokens"],
                "tokens_saved": r["tokens_saved"],
                "duration_ms": r["duration_ms"],
                "repo_name": r["repo_name"],
                "branch": r.get("branch"),
                "commit_sha": r.get("commit_sha"),
                "model_name": r.get("model_name", "gpt-4o"),
                "timestamp": r["timestamp"],
            })

        batch_payload = {"events": payload_events}

        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.post(
                    f"{base_url}{BATCH_SYNC_PATH}",
                    json=batch_payload,
                    headers=headers,
                )
                if res.status_code in (404, 405):
                    # Fallback to single item endpoint if batch route is not present
                    for event in payload_events:
                        res = client.post(
                            f"{base_url}{SINGLE_SYNC_PATH}",
                            json=event,
                            headers=headers,
                        )
                        if res.status_code >= 400:
                            return (
                                False,
                                f"Failed to sync metric to {base_url}: HTTP {res.status_code} {res.text}",
                                total_synced,
                            )
                elif res.status_code >= 400:
                    return (
                        False,
                        f"Failed to sync batch to {base_url}: HTTP {res.status_code} {res.text}",
                        total_synced,
                    )

            store.mark_synced(ids)
            total_synced += len(ids)

        except Exception as exc:
            logger.debug("Sync failed with exception", exc_info=True)
            return False, f"Network or server error during sync: {exc}", total_synced

    repo_name = project_root.resolve().name
    org_suffix = f", org: {org}" if org else ""
    return (
        True,
        f"Successfully reported {total_synced} metrics to {base_url} (repo: {repo_name}{org_suffix}).",
        total_synced,
    )
