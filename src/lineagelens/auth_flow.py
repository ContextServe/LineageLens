"""CLI device-authorization flow for ContextServe.ai.

Implements the OTP + browser login dance inspired by ``gcloud auth login``
and ``gh auth login``:

1. Generate a one-time password (OTP) and a device session UUID.
2. Register the session with ContextServe via ``POST /api/v1/auth/device/start``.
3. Open the user's browser to the ContextServe CLI-login page.
4. Poll ``GET /api/v1/auth/device/status`` until the session is complete.
5. Exchange the session for an access token via ``POST /api/v1/auth/device/exchange``.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
import webbrowser
from importlib.metadata import version as pkg_version
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "httpx is required for auth commands. "
        "Install it with: pip install 'lineagelens[auth]'"
    ) from exc


# ─── Environment map ──────────────────────────────────────────────────────────

ENVIRONMENTS: dict[str, str] = {
    "prod":  "https://contextserve.ai",
    "stage": "https://stage.contextserve.ai",
    "dev":   "https://dev.contextserve.ai",
    "local": os.environ.get("CONTEXTSERVE_LOCAL_URL", "http://localhost:5173"),
}

# ─── OTP generation ───────────────────────────────────────────────────────────

# Excludes visually ambiguous characters: O (oh), 0 (zero), I (eye), 1 (one)
_OTP_ALPHABET = "BCDFGHJKLMNPQRSTVWXYZ23456789"
_OTP_RAW_LEN  = 8


def generate_otp() -> str:
    """Return a random 8-character OTP formatted as ``XXXX-YYYY``.

    Uses :mod:`secrets` for cryptographically secure randomness.
    Characters are drawn from a 29-char alphabet that omits ambiguous glyphs
    (O, 0, I, 1), giving roughly 40 bits of entropy.
    """
    raw = "".join(secrets.choice(_OTP_ALPHABET) for _ in range(_OTP_RAW_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def generate_device_session_id() -> str:
    """Return a fresh UUID v4 string to identify this device session."""
    return str(uuid.uuid4())


def _cli_version() -> str:
    try:
        return pkg_version("lineagelens")
    except Exception:
        return "unknown"


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=10)


# ─── Device flow steps ────────────────────────────────────────────────────────

class LoginError(Exception):
    """Raised when the login flow cannot complete."""


class LoginTimeout(LoginError):
    """Raised when the user doesn't authenticate within the allowed window."""


class LoginRejected(LoginError):
    """Raised when the user explicitly denies the authorization in the browser."""


def device_start(
    base_url: str,
    session_id: str,
    otp: str,
    env: str,
    cli_version: str | None = None,
) -> int:
    """Register the device session with ContextServe.

    Returns:
        The OTP TTL in seconds (typically 300).

    Raises:
        LoginError: On non-2xx response.
    """
    payload: dict[str, Any] = {
        "device_session_id": session_id,
        "otp": otp.replace("-", ""),   # strip hyphen before sending
        "env": env,
        "cli_version": cli_version or _cli_version(),
    }
    with _client(base_url) as client:
        resp = client.post("/api/v1/auth/device/start", json=payload)

    if resp.status_code == 409:
        raise LoginError("Device session ID collision — please retry.")
    if not resp.is_success:
        detail = resp.json().get("detail", resp.text) if resp.content else resp.text
        raise LoginError(f"Could not start device session: {detail}")

    return resp.json().get("expires_in", 300)


def poll_device_status(
    base_url: str,
    session_id: str,
    timeout: int = 300,
    interval: int = 2,
) -> dict[str, Any]:
    """Poll the device status endpoint until the session is complete.

    Returns:
        The final status JSON payload (with ``email`` key populated).

    Raises:
        LoginTimeout:  User didn't authenticate within *timeout* seconds.
        LoginRejected: User clicked Deny in the browser.
        LoginError:    Unexpected status or network failure.
    """
    spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    deadline = time.monotonic() + timeout
    frame_idx = 0

    while time.monotonic() < deadline:
        try:
            with _client(base_url) as client:
                resp = client.get(
                    "/api/v1/auth/device/status",
                    params={"session": session_id},
                )
        except httpx.RequestError as exc:
            raise LoginError(f"Network error while polling: {exc}") from exc

        if not resp.is_success:
            raise LoginError(f"Unexpected error polling status: {resp.status_code}")

        data = resp.json()
        status = data.get("status", "pending")

        if status == "complete":
            # Clear the spinner line
            print("\r" + " " * 50 + "\r", end="", flush=True)
            return data

        if status == "expired":
            print()
            raise LoginTimeout(
                "The one-time code expired before you completed authentication.\n"
                "Run: lineagelens auth login"
            )

        if status == "rejected":
            print()
            raise LoginRejected(
                "You denied the CLI authorization request in the browser."
            )

        # status == "pending" — keep spinning
        spinner = spinner_frames[frame_idx % len(spinner_frames)]
        remaining = int(deadline - time.monotonic())
        print(
            f"\r  Waiting for authentication  {spinner}  ({remaining}s remaining) ",
            end="",
            flush=True,
        )
        frame_idx += 1
        time.sleep(interval)

    raise LoginTimeout(
        "Timed out waiting for browser authentication.\n"
        "Run: lineagelens auth login"
    )


def device_exchange(base_url: str, session_id: str) -> dict[str, Any]:
    """Exchange a completed device session for an access token.

    Returns:
        The full ``TokenResponse`` dict from ContextServe.

    Raises:
        LoginError: If the session is not complete or was already consumed.
    """
    with _client(base_url) as client:
        resp = client.post(
            "/api/v1/auth/device/exchange",
            json={"device_session_id": session_id},
        )

    if resp.status_code == 410:
        raise LoginError(
            "Session not yet confirmed or already consumed. "
            "Run: lineagelens auth login"
        )
    if resp.status_code == 403:
        raise LoginError("Session was rejected. Run: lineagelens auth login")
    if not resp.is_success:
        detail = resp.json().get("detail", resp.text) if resp.content else resp.text
        raise LoginError(f"Token exchange failed: {detail}")

    return resp.json()


# ─── High-level entry point ───────────────────────────────────────────────────

def run_login(
    env: str = "prod",
    no_browser: bool = False,
    timeout: int = 300,
) -> dict[str, Any]:
    """Execute the full device-auth login flow.

    Returns:
        The ``TokenResponse`` dict (access_token, user_id, org_id, …).

    Raises:
        LoginError / LoginTimeout / LoginRejected on failure.
    """
    if env not in ENVIRONMENTS:
        raise LoginError(
            f"Unknown environment '{env}'. "
            f"Choose from: {', '.join(ENVIRONMENTS)}"
        )

    base_url   = ENVIRONMENTS[env]
    session_id = generate_device_session_id()
    otp        = generate_otp()
    otp_raw    = otp.replace("-", "")

    # 1. Register session
    try:
        device_start(base_url, session_id, otp_raw, env)
    except httpx.ConnectError as exc:
        raise LoginError(
            f"Could not reach {base_url}.\n"
            "  \u2022 Check your internet connection.\n"
            "  \u2022 If using --env local, make sure ContextServe is running "
            "(docker compose up)."
        ) from exc

    login_url = (
        f"{base_url}/cli-login"
        f"?session={session_id}"
        f"&env={env}"
    )

    _dash = "\u2500"
    _corner_tl = "\u250c"
    _corner_tr = "\u2510"
    _corner_bl = "\u2514"
    _corner_br = "\u2518"
    _pipe = "\u2502"

    print()
    print("  \u2746 LineageLens \u00d7 ContextServe.ai")
    print(f"  {_dash * 51}")
    print(f"  Environment : {env}  ({base_url})")
    print()

    if no_browser:
        print("  Open this URL in a browser to authenticate:")
        print(f"    {login_url}")
    else:
        print("  Opening your browser for authentication\u2026")
        print("  If it didn't open automatically, visit:")
        print(f"    {login_url}")
        webbrowser.open(login_url)

    print()
    print(f"  {_corner_tl}{_dash * 41}{_corner_tr}")
    print(f"  {_pipe}   Your one-time code:  {otp:<16}   {_pipe}")
    print(f"  {_pipe}   Enter this code in the browser tab   {_pipe}")
    print(f"  {_corner_bl}{_dash * 41}{_corner_br}")
    print()

    # 3. Poll
    token_response = poll_device_status(base_url, session_id, timeout=timeout)

    # 4. Exchange (status is complete — email is in the status response,
    #    but we need the access token from exchange)
    token_response = device_exchange(base_url, session_id)

    return token_response
