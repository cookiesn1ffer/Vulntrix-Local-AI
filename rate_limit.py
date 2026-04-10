"""
rate_limit.py — In-memory sliding-window rate limiter for Vulntrix.

No extra dependencies. Uses a deque per client IP to track request timestamps.

Configuration (via environment variables):
  RATE_LIMIT_REQUESTS  — max requests per window (default 60)
  RATE_LIMIT_WINDOW    — window size in seconds   (default 60)
  RATE_LIMIT_WS        — max WS handshakes/min    (default 10)

Usage in web_server.py:
  from rate_limit import RateLimitMiddleware
  app.add_middleware(RateLimitMiddleware)
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ── Config ────────────────────────────────────────────────────────────────────
MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS", 60))   # per window
WINDOW_SECS  = int(os.environ.get("RATE_LIMIT_WINDOW",   60))   # seconds
WS_MAX       = int(os.environ.get("RATE_LIMIT_WS",       10))   # WS per minute

# Endpoints with tighter per-IP limits (still within the global budget).
# Format: path → (max_requests, window_seconds)
_TIGHT_PATHS: dict[str, tuple[int, int]] = {
    # Auth — brute-force guard
    "/api/auth/verify":         (5,  60),   # 5 login attempts / minute
    # Heavy AI generation endpoints — each call blocks a thread for seconds
    "/api/report/generate":     (2,  60),   # 2 full reports / minute
    "/api/privesc/checklist":   (6,  60),   # 6 checklists / minute
    "/api/phishing/generate":   (6,  60),   # 6 phishing templates / minute
    "/api/postex/build":        (6,  60),   # 6 post-ex builds / minute
    "/api/payload/obfuscate":   (8,  60),   # 8 obfuscations / minute
    "/api/wordlist/generate":   (8,  60),   # 8 wordlists / minute
}

# Paths exempt from rate limiting (health probes, static assets)
_EXEMPT_PREFIXES = ("/static/", "/favicon")
_EXEMPT_EXACT    = {"/health", "/api/health"}


# ── Store ─────────────────────────────────────────────────────────────────────
_buckets: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _client_ip(request: Request) -> str:
    """Best-effort client IP extraction (handles reverse proxies)."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_allowed(key: str, max_req: int, window: int) -> bool:
    """Sliding-window check. Returns True if the request is within limits."""
    now = time.monotonic()
    cutoff = now - window
    with _lock:
        dq = _buckets[key]
        # Evict expired timestamps
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= max_req:
            return False
        dq.append(now)
        return True


# ── Middleware ────────────────────────────────────────────────────────────────
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        # Exempt paths
        if path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        ip = _client_ip(request)

        # Per-endpoint tight limits
        if path in _TIGHT_PATHS:
            limit, window = _TIGHT_PATHS[path]
            key = f"tight:{ip}:{path}"
            if not _is_allowed(key, limit, window):
                return JSONResponse(
                    status_code=429,
                    content={"error": "Too many requests — slow down"},
                    headers={"Retry-After": str(window)},
                )

        # Global per-IP limit
        key = f"global:{ip}"
        if not _is_allowed(key, MAX_REQUESTS, WINDOW_SECS):
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded"},
                headers={"Retry-After": str(WINDOW_SECS)},
            )

        return await call_next(request)


# ── WebSocket helper (called before ws.accept()) ──────────────────────────────
def ws_allowed(ip: str) -> bool:
    """Returns True if the IP may open a new WebSocket connection."""
    return _is_allowed(f"ws:{ip}", WS_MAX, 60)
