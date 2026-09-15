# LineageLens CLI — `auth login` Specification

> **Version:** 1.0  
> **Status:** Ready for implementation  
> **Applies to:** `lineagelens` CLI (Python) · ContextServe.ai backend (FastAPI) · ContextServe.ai frontend (React/Vite)

---

## 1. Overview

`lineagelens auth login` provides a secure, interactive authentication flow for connecting the local CLI to a ContextServe.ai environment (`prod`, `stage`, `dev`, or `local`).

The flow mirrors `gcloud auth login` / `gh auth login`:

1. CLI generates a short-lived **OTP** and a **device session ID**.
2. CLI calls `POST /api/v1/auth/device/start` on ContextServe to register the session.
3. CLI opens the user's browser to the ContextServe login portal, pre-filling the OTP.
4. User authenticates (email + password, Google, or SAML SSO) and confirms the OTP in the browser.
5. CLI polls `GET /api/v1/auth/device/status` until the session is marked `complete`.
6. CLI calls `POST /api/v1/auth/device/exchange` to receive a long-lived access token.
7. CLI writes the token to `~/.config/lineagelens/credentials.json` and prints success.

---

## 2. Environment Map

| Alias | Base URL | Notes |
|---|---|---|
| `prod` | `https://app.contextserve.ai` | Default |
| `stage` | `https://stage.contextserve.ai` | |
| `dev` | `https://dev.contextserve.ai` | |
| `local` | `http://localhost:5173` | Override with `CONTEXTSERVE_LOCAL_URL` |

The local base URL matches the running Vite dev server (`docker compose up` serves frontend on `:5173`, backend on `:8000` behind `/api`).

---

## 3. Command Surface

### 3.1 New `auth` command group in `cli.py`

```
lineagelens auth <subcommand> [flags]
```

| Subcommand | Description |
|---|---|
| `login` | Start the OTP + browser login flow |
| `logout` | Revoke token server-side, remove from credentials file |
| `status` | Print authenticated identity and token expiry per env |
| `token` | Print raw bearer token to stdout (for scripts) |
| `switch-env` | Change the active environment without re-authenticating |

### 3.2 `auth login` flags

| Flag | Short | Default | Description |
|---|---|---|---|
| `--env` | `-e` | `prod` | Target environment: `prod`, `stage`, `dev`, `local` |
| `--no-browser` | | `false` | Print URL instead of opening browser (SSH-friendly) |
| `--reauth` | | `false` | Force re-authentication even if a valid token exists |
| `--timeout` | | `300` | Seconds to wait for browser login to complete |

### 3.3 `auth logout` flags

| Flag | Default | Description |
|---|---|---|
| `--env` | `prod` | Environment to log out of |
| `--all` | `false` | Log out of all environments at once |

---

## 4. Full Login Sequence

```
Terminal (lineagelens)             ContextServe Backend            Browser
─────────────────────              ─────────────────────            ───────
auth login --env local
  │
  ├─ generate OTP "KRTM-9P2V"
  ├─ generate device_session_id (UUID)
  │
  ├─ POST /api/v1/auth/device/start ──────────────────────────────►
  │    { device_session_id, otp, cli_version, env }
  │  ◄──────────────────────────────── 200 { expires_in: 300 }
  │
  ├─ open browser ─────────────────────────────────────────────────►
  │    http://localhost:5173/cli-login
  │      ?session=<device_session_id>&otp=KRTM-9P2V&env=local
  │
  ├─ print to terminal:
  │    ✦ Opening browser for authentication…
  │    ┌──────────────────────────────────────┐
  │    │  Your one-time code:  KRTM-9P2V      │
  │    │  Expires in 5 minutes                │
  │    └──────────────────────────────────────┘
  │    Waiting for authentication ⠋
  │
  │                                                    [User logs in via
  │                                                     existing AuthModal,
  │                                                     sees OTP confirmation
  │                                                     UI, clicks Authorize]
  │                                                            │
  │                                              PUT /api/v1/auth/device/confirm
  │                                                { session_id, otp } ◄─────┘
  │                                              200 OK (session marked complete)
  │
  ├─ poll GET /api/v1/auth/device/status?session=<id>  (every 2 s)
  │  ◄──────── { status: "complete", email: "ram@primeya.in" }
  │
  ├─ POST /api/v1/auth/device/exchange ───────────────────────────►
  │    { device_session_id }
  │  ◄──── { access_token, expires_in, user_id, org_id, org_name, plan_tier }
  │
  ├─ write ~/.config/lineagelens/credentials.json  (chmod 0600)
  └─ print: ✓ Logged in as ram@primeya.in (local)
```

---

## 5. OTP Design

| Property | Value |
|---|---|
| Format | `XXXX-YYYY` — 8 uppercase alphanumeric characters |
| Character set | `BCDFGHJKLMNPQRSTVWXYZ23456789` (no O, 0, I, 1 to avoid visual ambiguity) |
| Entropy | ~40 bits |
| Lifetime | 300 seconds (5 minutes) |
| One-time use | Invalidated on first successful exchange |

---

## 6. Credentials File

**Location:** `~/.config/lineagelens/credentials.json`  
**Permissions:** `0600` enforced on every write.

```json
{
  "version": 1,
  "active_env": "local",
  "environments": {
    "local": {
      "email": "ram@primeya.in",
      "access_token": "<jwt>",
      "expires_at": "2026-09-21T03:20:00Z",
      "user_id": "<uuid>",
      "organization_id": "<uuid>",
      "organization_name": "Primeya",
      "plan_tier": "developer_free",
      "base_url": "http://localhost:5173"
    },
    "prod": { "...": "..." }
  }
}
```

Override path with `LINEAGELENS_CREDENTIALS_FILE` env var.  
Credentials are **never** written to `lineagelens.yaml`.

---

## 7. Terminal UX

### 7.1 Successful login

```
$ lineagelens auth login --env local

  ✦ LineageLens × ContextServe.ai
  ─────────────────────────────────────────────────────
  Environment : local  (http://localhost:5173)

  Opening your browser for authentication…
  If it didn't open, visit:
    http://localhost:5173/cli-login?session=a7f3c2…&otp=KRTM-9P2V

  ┌─────────────────────────────────────┐
  │   Your one-time code:  KRTM-9P2V   │
  │   This code expires in 5 minutes   │
  └─────────────────────────────────────┘

  Waiting for authentication ⠋
  Waiting for authentication ⠙

  ✓ Logged in as ram@primeya.in
  ✓ Environment : local
  ✓ Organization: Primeya  (developer_free)
  ✓ Token valid until: 2026-09-21 03:20 UTC

  Next steps:
    lineagelens auth switch-env prod   # switch environment
    lineagelens auth status            # view all sessions
```

### 7.2 Already authenticated

```
$ lineagelens auth login --env local

  ✓ Already logged in as ram@primeya.in (local)
  Token valid until: 2026-09-21 03:20 UTC

  To re-authenticate:          lineagelens auth login --reauth
  To login to another env:     lineagelens auth login --env prod
```

### 7.3 `auth status`

```
$ lineagelens auth status

  Environment   User                  Status      Expires
  ───────────────────────────────────────────────────────────────────
  local   ✓     ram@primeya.in        active      2026-09-21 03:20 UTC
  prod    ✗     (not authenticated)
  stage   ✗     (not authenticated)
  dev     ✗     (not authenticated)

  Active environment: local
```

### 7.4 No-browser / SSH mode

```
$ lineagelens auth login --no-browser --env prod

  Open this URL in a browser to authenticate:
    https://app.contextserve.ai/cli-login?session=a7f3…&otp=KRTM-9P2V

  Your one-time code:  KRTM-9P2V   (expires in 5 min)

  Waiting for authentication ⠋
```

---

## 8. Backend — New API Endpoints

All new routes live in `backend/app/routers/auth_router.py` under the existing `router` (prefix `/api/v1/auth`).

A new SQLAlchemy model `CliDeviceSession` is added to `backend/app/models.py`.

### 8.1 New model: `CliDeviceSession`

```python
# backend/app/models.py  (add to existing file)

class CliDeviceSession(Base):
    __tablename__ = "cli_device_sessions"

    id               = Column(String, primary_key=True, default=generate_uuid)
    session_id       = Column(String, unique=True, index=True, nullable=False)  # UUID from CLI
    otp_hash         = Column(String, nullable=False)   # SHA-256(otp + SECRET_KEY)
    env              = Column(String, default="prod")
    cli_version      = Column(String, nullable=True)
    status           = Column(String, default="pending")  # pending | complete | expired | rejected
    user_id          = Column(String, ForeignKey("users.id"), nullable=True)
    expires_at       = Column(DateTime, nullable=False)
    created_at       = Column(DateTime, default=datetime.utcnow)
    confirmed_at     = Column(DateTime, nullable=True)

    user = relationship("User")
```

### 8.2 `POST /api/v1/auth/device/start`

Registers a new device session initiated by the CLI.

**Request body:**
```json
{
  "device_session_id": "550e8400-e29b-41d4-a716-446655440000",
  "otp": "KRTM9P2V",
  "cli_version": "0.2.0",
  "env": "local"
}
```

**Response `200`:**
```json
{ "expires_in": 300 }
```

**Response `409`:** Session ID already registered (CLI regenerates and retries).

**Implementation:**
```python
@router.post("/device/start")
def device_start(req: DeviceStartRequest, db: Session = Depends(get_db)):
    existing = db.query(CliDeviceSession).filter(
        CliDeviceSession.session_id == req.device_session_id
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Session already registered")

    otp_hash = hashlib.sha256(
        (req.otp + settings.SECRET_KEY).encode()
    ).hexdigest()

    session = CliDeviceSession(
        session_id=req.device_session_id,
        otp_hash=otp_hash,
        env=req.env,
        cli_version=req.cli_version,
        expires_at=datetime.utcnow() + timedelta(seconds=300),
    )
    db.add(session)
    db.commit()
    return {"expires_in": 300}
```

---

### 8.3 `GET /api/v1/auth/device/status`

CLI polls this every 2 seconds.

**Query params:** `session=<device_session_id>`

**Response:**
```json
{
  "status": "pending",
  "email": null
}
```
```json
{
  "status": "complete",
  "email": "ram@primeya.in"
}
```

Statuses: `pending` · `complete` · `expired` · `rejected`

**Implementation:**
```python
@router.get("/device/status")
def device_status(session: str, db: Session = Depends(get_db)):
    s = db.query(CliDeviceSession).filter(
        CliDeviceSession.session_id == session
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    # Auto-expire
    if s.status == "pending" and datetime.utcnow() > s.expires_at:
        s.status = "expired"
        db.commit()

    email = s.user.email if s.user else None
    return {"status": s.status, "email": email}
```

---

### 8.4 `PUT /api/v1/auth/device/confirm`

Called by the **browser** (frontend) after the user authenticates and clicks "Authorize CLI".

**Request body (Authorization: Bearer `<user's access_token>`):**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "otp": "KRTM9P2V"
}
```

**Response `200`:**
```json
{ "ok": true }
```

**Response `410`:** Session expired.  
**Response `422`:** OTP mismatch.

**Implementation:**
```python
@router.put("/device/confirm")
def device_confirm(
    req: DeviceConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    s = db.query(CliDeviceSession).filter(
        CliDeviceSession.session_id == req.session_id,
        CliDeviceSession.status == "pending"
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found or already used")

    if datetime.utcnow() > s.expires_at:
        s.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Session expired")

    expected_hash = hashlib.sha256(
        (req.otp + settings.SECRET_KEY).encode()
    ).hexdigest()
    if not hmac.compare_digest(s.otp_hash, expected_hash):
        raise HTTPException(status_code=422, detail="Invalid OTP")

    s.status = "complete"
    s.user_id = current_user.id
    s.confirmed_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
```

---

### 8.5 `POST /api/v1/auth/device/exchange`

Called by the CLI once polling returns `complete`. Returns a standard access token.

**Request body:**
```json
{ "device_session_id": "550e8400-e29b-41d4-a716-446655440000" }
```

**Response `200`** (matches existing `TokenResponse` schema):
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user_id": "...",
  "organization_id": "...",
  "organization_name": "Primeya",
  "plan_tier": "developer_free"
}
```

**Response `410`:** Session expired or not yet confirmed.  
**Response `403`:** Session rejected.

**Implementation:**
```python
@router.post("/device/exchange", response_model=TokenResponse)
def device_exchange(req: DeviceExchangeRequest, db: Session = Depends(get_db)):
    s = db.query(CliDeviceSession).filter(
        CliDeviceSession.session_id == req.device_session_id,
        CliDeviceSession.status == "complete"
    ).first()
    if not s:
        raise HTTPException(status_code=410, detail="Session not complete or already consumed")

    user = s.user
    membership = db.query(TeamMember).filter(TeamMember.user_id == user.id).first()
    org = db.query(Organization).filter(Organization.id == membership.organization_id).first()

    # Mark consumed so it can't be exchanged twice
    s.status = "consumed"
    db.commit()

    token = create_access_token({"sub": user.id, "org_id": org.id})
    return TokenResponse(
        access_token=token,
        user_id=user.id,
        organization_id=org.id,
        organization_name=org.name,
        plan_tier=org.plan_tier,
    )
```

---

### 8.6 `POST /api/v1/auth/device/reject`  _(optional)_

Browser-side — user clicks "Deny" instead of "Authorize".

**Request body (Authorization: Bearer `<user's access_token>`):**
```json
{ "session_id": "..." }
```

**Response `200`:** `{ "ok": true }`

---

## 9. Frontend — New `/cli-login` Page

### 9.1 Route

Add a new route `/cli-login` to `frontend/src/App.jsx`.

**URL params read from `window.location.search`:**
- `session` — device session ID
- `otp` — OTP displayed on screen for user to verify visually
- `env` — environment label (shown in UI)

### 9.2 Page states

```
State 1: Not authenticated
  → Show the existing AuthModal inline (not a modal popup)
  → After login, advance to State 2

State 2: Authenticated — pending OTP confirmation
  → Show "Authorize CLI Access" panel:
     - Organization name + user email
     - Environment badge (env param)
     - One-time code display: KRTM-9P2V
     - "Authorize" button → PUT /api/v1/auth/device/confirm
     - "Deny" button     → POST /api/v1/auth/device/reject

State 3: Confirmed
  → Show: "✓ Authorization successful! Return to your terminal."
  → Auto-close tab after 3 seconds (window.close())

State 4: Expired / Error
  → Show: "✗ This authorization request has expired."
  → Link to run lineagelens auth login again
```

### 9.3 Component: `CLILoginPage.jsx`

```
frontend/src/components/CLILoginPage.jsx   [NEW]
```

Key implementation points:
- Reads `session`, `otp`, `env` from `URLSearchParams`.
- Checks `localStorage` for existing `access_token` (set by `AuthModal` on normal login). If present, skip to State 2.
- On "Authorize" click:
  ```js
  await fetch('/api/v1/auth/device/confirm', {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({ session_id: session, otp })
  });
  ```
- Transitions to State 3 on success, State 4 on expired/error.

---

## 10. CLI-side Implementation Plan

### New file: `src/lineagelens/auth_flow.py`

```python
ENVIRONMENTS = {
    "prod":  "https://app.contextserve.ai",
    "stage": "https://stage.contextserve.ai",
    "dev":   "https://dev.contextserve.ai",
    "local": os.environ.get("CONTEXTSERVE_LOCAL_URL", "http://localhost:5173"),
}

OTP_ALPHABET = "BCDFGHJKLMNPQRSTVWXYZ23456789"  # 30 chars, no ambiguous O/0/I/1

def generate_otp() -> str:
    raw = "".join(secrets.choice(OTP_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"

def generate_device_session() -> str:
    return str(uuid.uuid4())

def device_start(base_url, session_id, otp, env, cli_version) -> None: ...
def poll_device_status(base_url, session_id, timeout=300, interval=2) -> dict: ...
def device_exchange(base_url, session_id) -> dict: ...   # returns TokenResponse dict
```

### New file: `src/lineagelens/credentials.py`

```python
DEFAULT_CREDS_PATH = Path.home() / ".config" / "lineagelens" / "credentials.json"

class CredentialsStore:
    def load(self) -> dict: ...
    def save(self, env: str, token_response: dict, base_url: str) -> None: ...
    def get(self, env: str) -> dict | None: ...
    def remove(self, env: str) -> None: ...
    @property
    def active_env(self) -> str: ...
    def set_active_env(self, env: str) -> None: ...
```

### Updated file: `src/lineagelens/cli.py`

Add `auth` subparser group to existing `argparse` `main()`:

```python
auth_cmd = commands.add_parser("auth", help="Authenticate with ContextServe.ai")
auth_sub = auth_cmd.add_subparsers(dest="auth_command", required=True)

login_cmd = auth_sub.add_parser("login")
login_cmd.add_argument("--env", "-e", default="prod",
                        choices=["prod","stage","dev","local"])
login_cmd.add_argument("--no-browser", action="store_true")
login_cmd.add_argument("--reauth", action="store_true")
login_cmd.add_argument("--timeout", type=int, default=300)

logout_cmd = auth_sub.add_parser("logout")
logout_cmd.add_argument("--env", "-e", default="prod",
                         choices=["prod","stage","dev","local"])
logout_cmd.add_argument("--all", action="store_true")

status_cmd  = auth_sub.add_parser("status")
token_cmd   = auth_sub.add_parser("token")
token_cmd.add_argument("--env", "-e", default="prod")

switch_cmd  = auth_sub.add_parser("switch-env")
switch_cmd.add_argument("env", choices=["prod","stage","dev","local"])
```

### Dependencies to add (`pyproject.toml`)

| Package | Reason |
|---|---|
| `httpx>=0.27` | HTTP client for device flow polling (already an optional dep under `llm` — promote to core) |

---

## 11. Error Handling

| Condition | Terminal message | Exit code |
|---|---|---|
| OTP expired (timeout) | `✗ Login timed out. Run lineagelens auth login to try again.` | `1` |
| Session rejected by user | `✗ Login denied — you clicked Deny in the browser.` | `1` |
| Network / connection error | `✗ Could not reach <base_url>. Check your connection and --env.` | `1` |
| Token already valid (no `--reauth`) | Inform + exit cleanly | `0` |
| Unknown env | `✗ Unknown environment 'xyz'. Choose: prod, stage, dev, local` | `1` |
| Credentials file write error | `✗ Cannot write to <path>. Check permissions.` | `1` |

---

## 12. File Change Summary

### ContextServe backend

| File | Change |
|---|---|
| `backend/app/models.py` | Add `CliDeviceSession` model |
| `backend/app/routers/auth_router.py` | Add `device/start`, `device/status`, `device/confirm`, `device/exchange`, `device/reject` endpoints |

### ContextServe frontend

| File | Change |
|---|---|
| `frontend/src/App.jsx` | Add `/cli-login` route |
| `frontend/src/components/CLILoginPage.jsx` | **New** — OTP confirmation page (3-state flow) |

### LineageLens CLI

| File | Change |
|---|---|
| `src/lineagelens/auth_flow.py` | **New** — OTP generation, device session HTTP calls |
| `src/lineagelens/credentials.py` | **New** — credentials file read/write |
| `src/lineagelens/cli.py` | Add `auth` command group + subcommands |
| `pyproject.toml` | Promote `httpx` from optional to core dependency |

---

## 13. Testing Plan

| Test | Type | File |
|---|---|---|
| `test_generate_otp_format` | Unit | `tests/test_auth_flow.py` |
| `test_otp_excludes_ambiguous_chars` | Unit | `tests/test_auth_flow.py` |
| `test_credentials_store_roundtrip` | Unit | `tests/test_credentials.py` |
| `test_credentials_chmod` | Unit | `tests/test_credentials.py` |
| `test_device_start_registers_session` | API | `backend/tests/test_device_auth.py` |
| `test_device_status_pending` | API | `backend/tests/test_device_auth.py` |
| `test_device_confirm_wrong_otp` | API | `backend/tests/test_device_auth.py` |
| `test_device_confirm_expired` | API | `backend/tests/test_device_auth.py` |
| `test_device_exchange_returns_token` | API | `backend/tests/test_device_auth.py` |
| `test_device_exchange_double_use` | API | `backend/tests/test_device_auth.py` |
| `test_poll_timeout_raises` | Unit | `tests/test_auth_flow.py` |
| `test_cli_auth_status_no_token` | CLI | `tests/test_cli_auth.py` |

---

## 14. Open Questions

1. **Token lifetime** — `ACCESS_TOKEN_EXPIRE_MINUTES` is currently 7 days for all users. Should CLI tokens use a different (longer? shorter?) lifetime?

2. **Refresh tokens** — The current auth system issues only access tokens (no refresh token). Should the device exchange return a longer-lived CLI-specific token, or is 7 days acceptable and users simply re-run `lineagelens auth login`?

3. **`/cli-login` route protection** — Should the page be accessible only when a valid `?session=` param is present, or should it redirect to home if accessed directly?

4. **CI / non-interactive mode** — For CI pipelines, recommendation is to export `CONTEXTSERVE_TOKEN=<token>` (bypassing the credentials file). Should this be documented in the spec now?

5. **Database migration** — The project uses SQLAlchemy with `create_all`. Adding `CliDeviceSession` to `models.py` will auto-create the table on next startup. No Alembic migration needed for SQLite dev, but confirm for production Postgres if applicable.
