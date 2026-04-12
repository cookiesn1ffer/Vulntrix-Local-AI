"""
auth.py — Session-based authentication for Vulntrix.

How to enable
-------------
Create a .env file in the project root:

    BOT_SECRET=your-strong-password-here

Leave BOT_SECRET empty or omit the .env file to run with no auth
(safe for local-only use on a trusted machine).

Session flow
------------
1. Browser POSTs the BOT_SECRET to /api/auth/verify
2. Server returns a UUID session token (≠ the raw secret)
3. Frontend stores the session token in sessionStorage
4. All subsequent requests send:  X-Bot-Token: <session_token>
5. WebSocket connects with:       /ws/stream?token=<session_token>
6. Sessions expire after SESSION_TTL_HOURS (default 8 h)
7. /api/auth/refresh extends expiry; /api/auth/logout revokes immediately

No database required — sessions are in-memory (lost on server restart).
"""

from __future__ import annotations

import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from logger import get_logger

# FastAPI is imported lazily so auth.py can be imported in test environments
# that don't have fastapi installed (e.g. pure-logic unit tests).
if TYPE_CHECKING:
    from fastapi import Request
    from fastapi.responses import JSONResponse

log = get_logger("auth")

# ── Load .env manually — no extra dependency ──────────────────────────────────

def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.split("#")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), val)


_load_env(Path(__file__).parent / ".env")

SECRET           = os.environ.get("BOT_SECRET", "").strip()
AUTH_ENABLED     = bool(SECRET)
SESSION_TTL      = int(os.environ.get("SESSION_TTL_HOURS", "8")) * 3600  # seconds

if AUTH_ENABLED:
    log.info("Auth ENABLED — session TTL %d h", SESSION_TTL // 3600)
else:
    log.info("Auth DISABLED — running open (localhost only)")


# ── Session store ─────────────────────────────────────────────────────────────

# Maps session_token (UUID) → expiry timestamp (float)
_sessions: dict[str, float] = {}
_sessions_lock = threading.Lock()


def _purge_expired() -> None:
    """Remove stale sessions (called lazily — no background thread needed)."""
    now = time.time()
    with _sessions_lock:
        stale = [tok for tok, exp in _sessions.items() if exp < now]
        for tok in stale:
            del _sessions[tok]


def create_session(ip: str = "") -> str:
    """Validate secret, then mint a fresh session token."""
    _purge_expired()
    token = str(uuid.uuid4())
    with _sessions_lock:
        _sessions[token] = time.time() + SESSION_TTL
    log.info("Session created ip=%s expires_in=%dh", ip or "unknown", SESSION_TTL // 3600)
    return token


def refresh_session(token: str) -> bool:
    """Extend an existing session's TTL. Returns False if session not found / expired."""
    _purge_expired()
    now = time.time()
    with _sessions_lock:
        if token in _sessions and _sessions[token] > now:
            _sessions[token] = now + SESSION_TTL
            return True
    return False


def revoke_session(token: str) -> None:
    """Immediately invalidate a session (logout)."""
    with _sessions_lock:
        _sessions.pop(token, None)
    log.info("Session revoked")


def session_expires_at(token: str) -> Optional[float]:
    """Return UNIX expiry timestamp for the token, or None if not found."""
    with _sessions_lock:
        return _sessions.get(token)


# ── Paths that skip authentication ────────────────────────────────────────────

_ALWAYS_PUBLIC   = {"/", "/login", "/health"}
_PUBLIC_PREFIXES = ("/static/", "/api/auth/", "/api/health")


# ── FastAPI middleware ────────────────────────────────────────────────────────

async def auth_middleware(request: "Request", call_next):
    """Reject non-authenticated requests when auth is enabled."""
    from fastapi.responses import JSONResponse  # lazy import

    if not AUTH_ENABLED:
        return await call_next(request)

    path = request.url.path

    if path in _ALWAYS_PUBLIC or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    token = (
        request.headers.get("X-Bot-Token", "")
        or request.query_params.get("token", "")
    )

    if not verify_token(token):
        log.warning("Rejected %s — bad/expired token", path)
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return await call_next(request)


# ── Token verification ────────────────────────────────────────────────────────

def verify_token(token: str) -> bool:
    """Return True if auth is disabled OR the token is a valid live session."""
    if not AUTH_ENABLED:
        return True
    if not token:
        return False
    _purge_expired()
    now = time.time()
    with _sessions_lock:
        exp = _sessions.get(token)
    return exp is not None and exp > now


def verify_secret(secret: str) -> bool:
    """
    Check the raw BOT_SECRET using constant-time comparison to prevent
    timing attacks that could leak the secret one byte at a time.
    """
    if not SECRET:
        return False
    # secrets.compare_digest pads to equal length and compares in O(n) constant time
    return secrets.compare_digest(
        secret.encode("utf-8"),
        SECRET.encode("utf-8"),
    )
