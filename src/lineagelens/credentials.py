"""Persistent credentials storage for ContextServe.ai tokens.

Tokens are stored in ``~/.config/lineagelens/credentials.json`` with
``0600`` permissions (user read/write only).

Environment variable override::

    LINEAGELENS_CREDENTIALS_FILE=/path/to/creds.json lineagelens auth login

Schema (version 1)::

    {
      "version": 1,
      "active_env": "prod",
      "environments": {
        "prod": {
          "email": "...",
          "access_token": "...",
          "expires_at": "2026-10-14T00:00:00Z",
          "user_id": "...",
          "organization_id": "...",
          "organization_name": "...",
          "plan_tier": "developer_free",
          "base_url": "https://app.contextserve.ai"
        }
      }
    }
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_DEFAULT_CREDS_PATH = Path.home() / ".config" / "lineagelens" / "credentials.json"


def _creds_path() -> Path:
    """Return the effective credentials file path.

    Respects the ``LINEAGELENS_CREDENTIALS_FILE`` environment variable.
    """
    env_override = os.environ.get("LINEAGELENS_CREDENTIALS_FILE")
    return Path(env_override) if env_override else _DEFAULT_CREDS_PATH


class CredentialsStore:
    """Read/write ContextServe access tokens keyed by environment."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _creds_path()
        self._data: dict[str, Any] = {}

    # ─── Internal I/O ─────────────────────────────────────────────────────

    def _load_raw(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": _SCHEMA_VERSION, "active_env": "prod", "environments": {}}
        try:
            text = self._path.read_text(encoding="utf-8")
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("credentials file is not a JSON object")
            return raw
        except (OSError, json.JSONDecodeError, ValueError):
            # Corrupt file — start fresh rather than crashing
            return {"version": _SCHEMA_VERSION, "active_env": "prod", "environments": {}}

    def _save_raw(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Write atomically to a temp file then rename
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # Enforce 0600 before moving into place
        with contextlib.suppress(OSError):
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)

        tmp.replace(self._path)
        self._data = data

    # ─── Public API ───────────────────────────────────────────────────────

    def load(self) -> CredentialsStore:
        """Read the credentials file from disk. Returns self for chaining."""
        self._data = self._load_raw()
        return self

    def save(
        self,
        env: str,
        token_response: dict[str, Any],
        base_url: str,
        email: str = "",
    ) -> None:
        """Persist a new token for the given environment.

        *token_response* is the dict returned by ``POST /api/v1/auth/device/exchange``
        which matches the ContextServe ``TokenResponse`` schema.
        """
        data = self._load_raw()
        envs: dict[str, Any] = data.setdefault("environments", {})

        # Compute expiry from ContextServe's ACCESS_TOKEN_EXPIRE_MINUTES (7 days default)
        # We don't get expires_in from device/exchange, so default to 7 days.
        from datetime import timedelta
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        envs[env] = {
            "email": email or token_response.get("email", ""),
            "access_token": token_response["access_token"],
            "expires_at": expires_at,
            "user_id": token_response.get("user_id", ""),
            "organization_id": token_response.get("organization_id", ""),
            "organization_name": token_response.get("organization_name", ""),
            "plan_tier": token_response.get("plan_tier", ""),
            "base_url": base_url,
        }
        data["active_env"] = env
        self._save_raw(data)

    def get(self, env: str) -> dict[str, Any] | None:
        """Return stored credentials for *env*, or ``None`` if not present."""
        data = self._load_raw()
        return data.get("environments", {}).get(env)

    def remove(self, env: str) -> bool:
        """Delete credentials for *env*.

        Returns:
            ``True`` if the entry existed and was removed, ``False`` otherwise.
        """
        data = self._load_raw()
        envs: dict[str, Any] = data.get("environments", {})
        if env not in envs:
            return False
        del envs[env]
        if data.get("active_env") == env:
            remaining = list(envs.keys())
            data["active_env"] = remaining[0] if remaining else "prod"
        self._save_raw(data)
        return True

    def remove_all(self) -> None:
        """Delete all stored credentials."""
        self._save_raw({"version": _SCHEMA_VERSION, "active_env": "prod", "environments": {}})

    @property
    def active_env(self) -> str:
        """Return the currently active environment name."""
        return self._load_raw().get("active_env", "prod")

    def set_active_env(self, env: str) -> None:
        """Persist a new active environment."""
        data = self._load_raw()
        data["active_env"] = env
        self._save_raw(data)

    def all_envs(self) -> dict[str, dict[str, Any]]:
        """Return all stored environment entries."""
        return dict(self._load_raw().get("environments", {}))

    def is_token_valid(self, env: str) -> bool:
        """Return True if a non-expired token exists for *env*."""
        creds = self.get(env)
        if not creds or not creds.get("access_token"):
            return False
        expires_at_str = creds.get("expires_at", "")
        if not expires_at_str:
            return True  # unknown expiry — assume valid
        try:
            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            return datetime.now(timezone.utc) < expires_at
        except ValueError:
            return True
