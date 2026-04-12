"""
security_headers.py — HTTP security headers middleware for Vulntrix.

Adds the following response headers on every reply:
  - Content-Security-Policy   (strict, no CDN — everything is local)
  - X-Frame-Options           (clickjacking protection)
  - X-Content-Type-Options    (MIME sniffing protection)
  - Referrer-Policy           (no referrer leakage)
  - Permissions-Policy        (deny camera/mic/geo)
  - Strict-Transport-Security (HSTS — activates only over HTTPS)

No external dependencies.
"""

from __future__ import annotations

from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ── CSP ───────────────────────────────────────────────────────────────────────
# Everything is served locally — no CDN, no external connections needed.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",   # unsafe-inline needed for onclick= attrs
    "style-src 'self' 'unsafe-inline'",    # inline styles used throughout
    "img-src 'self' data:",                # data: URIs for favicon/inline images
    "font-src 'self' data:",
    "connect-src 'self' ws: wss:",         # WebSocket connections
    "frame-ancestors 'none'",             # equivalent to X-Frame-Options: DENY
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

_HEADERS: dict[str, str] = {
    "Content-Security-Policy":   _CSP,
    "X-Frame-Options":           "DENY",
    "X-Content-Type-Options":    "nosniff",
    "Referrer-Policy":           "no-referrer",
    "Permissions-Policy":        "camera=(), microphone=(), geolocation=(), payment=()",
    # HSTS — browsers ignore this over plain HTTP, so safe to always send
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        for header, value in _HEADERS.items():
            response.headers[header] = value
        return response
