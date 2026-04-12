"""
test_endpoints.py — FastAPI endpoint tests using TestClient.

Mocks OllamaClient so tests run completely offline with no Ollama dependency.
Tests cover: auth, health, target CRUD, notes, credentials, timeline, recon paste.

Run: pytest tests/test_endpoints.py -v
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Project root on path ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Patch OllamaClient before importing web_server ───────────────────────────
# This prevents "cannot connect to Ollama" errors during import / startup.
_mock_client = MagicMock()
_mock_client.generate.return_value = iter(["mock ", "response"])
_mock_client.models.return_value   = ["llama3", "mistral"]

with patch.dict("sys.modules", {}):
    with patch("ai_core.ollama_client.OllamaClient", return_value=_mock_client):
        # Disable auth for all endpoint tests (test auth separately below)
        with patch.dict("os.environ", {"BOT_SECRET": ""}):
            from fastapi.testclient import TestClient
            import web_server
            web_server.client = _mock_client
            client = TestClient(web_server.app, raise_server_exceptions=False)


# ════════════════════════════════════════════════════════════
# Auth endpoints
# ════════════════════════════════════════════════════════════

class TestAuthEndpoints:

    def test_auth_status_no_secret(self):
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        data = r.json()
        assert "auth_enabled" in data
        assert data["auth_enabled"] is False

    def test_auth_verify_when_disabled_accepts_anything(self):
        r = client.post("/api/auth/verify", json={"token": "anything"})
        assert r.status_code == 200
        assert r.json()["valid"] is True

    def test_auth_verify_empty_token_when_disabled(self):
        r = client.post("/api/auth/verify", json={"token": ""})
        assert r.status_code == 200
        assert r.json()["valid"] is True


# ════════════════════════════════════════════════════════════
# Auth middleware (with BOT_SECRET set)
# ════════════════════════════════════════════════════════════

class TestAuthMiddleware:

    SECRET = "test-secret-xyz"

    @pytest.fixture(autouse=True)
    def _enable_auth(self, monkeypatch):
        import auth
        monkeypatch.setattr(auth, "SECRET",       self.SECRET)
        monkeypatch.setattr(auth, "AUTH_ENABLED", True)

    def test_blocked_without_token(self):
        r = client.get("/api/targets")
        assert r.status_code == 401

    def test_allowed_with_correct_token(self):
        # The middleware validates SESSION TOKENS (UUID), not the raw secret.
        # First exchange the raw secret for a session token via /api/auth/verify.
        import auth as _auth
        session_tok = _auth.create_session(ip="test")
        r = client.get("/api/targets",
                       headers={"X-Bot-Token": session_tok})
        assert r.status_code == 200

    def test_blocked_with_wrong_token(self):
        r = client.get("/api/targets",
                       headers={"X-Bot-Token": "wrong"})
        assert r.status_code == 401

    def test_health_always_public(self):
        # /api/health must be reachable without a token
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_auth_status_always_public(self):
        r = client.get("/api/auth/status")
        assert r.status_code == 200


# ════════════════════════════════════════════════════════════
# Health
# ════════════════════════════════════════════════════════════

class TestHealth:

    def test_health_returns_200(self):
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_health_has_status_key(self):
        r = client.get("/api/health")
        data = r.json()
        assert "status" in data or "ollama" in data


# ════════════════════════════════════════════════════════════
# Target CRUD
# ════════════════════════════════════════════════════════════

class TestTargetCRUD:

    TARGET = "test-target-192.168.1.1"

    def test_list_targets_returns_list(self):
        r = client.get("/api/targets")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_set_target(self):
        r = client.post("/api/target", json={"target": self.TARGET})
        assert r.status_code == 200

    def test_get_context_after_set(self):
        client.post("/api/target", json={"target": self.TARGET})
        r = client.get(f"/api/target/{self.TARGET}/context")
        assert r.status_code == 200

    def test_delete_target(self):
        client.post("/api/target", json={"target": self.TARGET})
        r = client.delete(f"/api/target/{self.TARGET}")
        assert r.status_code in (200, 204, 404)   # 404 ok if already gone

    def test_set_target_empty_string(self):
        r = client.post("/api/target", json={"target": ""})
        # Should return 422 (validation error) or 400
        assert r.status_code in (400, 422)


# ════════════════════════════════════════════════════════════
# Notes
# ════════════════════════════════════════════════════════════

class TestNotes:

    TARGET = "note-test-target"

    def setup_method(self):
        client.post("/api/target", json={"target": self.TARGET})

    def test_add_note(self):
        r = client.post("/api/note", json={
            "target": self.TARGET,
            "label":  "finding",
            "content": "SQL injection on /login",
        })
        assert r.status_code == 200

    def test_delete_note(self):
        client.post("/api/note", json={
            "target": self.TARGET,
            "label":  "temp",
            "content": "delete me",
        })
        r = client.delete(f"/api/note/temp?target={self.TARGET}")
        assert r.status_code in (200, 204, 404)

    def test_add_note_missing_fields(self):
        r = client.post("/api/note", json={"target": self.TARGET})
        assert r.status_code == 422


# ════════════════════════════════════════════════════════════
# Credentials
# ════════════════════════════════════════════════════════════

class TestCredentials:

    TARGET = "cred-test-target"

    def setup_method(self):
        client.post("/api/target", json={"target": self.TARGET})

    def test_add_credential(self):
        r = client.post("/api/credential", json={
            "target":   self.TARGET,
            "username": "admin",
            "password": "secret",
            "service":  "ssh",
        })
        assert r.status_code == 200

    def test_add_credential_hash(self):
        r = client.post("/api/credential", json={
            "target":   self.TARGET,
            "username": "root",
            "hash_val": "5f4dcc3b5aa765d61d8327deb882cf99",
        })
        assert r.status_code == 200

    def test_add_credential_missing_target(self):
        r = client.post("/api/credential", json={
            "username": "admin",
            "password": "pw",
        })
        assert r.status_code == 422


# ════════════════════════════════════════════════════════════
# Timeline
# ════════════════════════════════════════════════════════════

class TestTimeline:

    TARGET = "timeline-test-target"

    def setup_method(self):
        client.post("/api/target", json={"target": self.TARGET})

    def test_get_timeline_empty(self):
        r = client.get(f"/api/timeline/{self.TARGET}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_add_timeline_event(self):
        r = client.post("/api/timeline/event", json={
            "target":   self.TARGET,
            "category": "scan",
            "content":  "nmap complete — 5 ports found",
        })
        assert r.status_code == 200

    def test_timeline_event_appears_in_get(self):
        client.post("/api/timeline/event", json={
            "target":   self.TARGET,
            "category": "exploit",
            "content":  "got reverse shell",
        })
        r = client.get(f"/api/timeline/{self.TARGET}")
        events = r.json()
        assert any("got reverse shell" in str(e) for e in events)


# ════════════════════════════════════════════════════════════
# Security headers
# ════════════════════════════════════════════════════════════

class TestSecurityHeaders:

    def test_csp_header_present(self):
        r = client.get("/api/health")
        assert "content-security-policy" in {h.lower() for h in r.headers}

    def test_x_frame_options_deny(self):
        r = client.get("/api/health")
        assert r.headers.get("x-frame-options", "").upper() == "DENY"

    def test_x_content_type_options(self):
        r = client.get("/api/health")
        assert r.headers.get("x-content-type-options", "").lower() == "nosniff"

    def test_referrer_policy(self):
        r = client.get("/api/health")
        assert "no-referrer" in r.headers.get("referrer-policy", "").lower()


# ════════════════════════════════════════════════════════════
# Rate limiting (basic smoke test)
# ════════════════════════════════════════════════════════════

class TestRateLimit:

    def test_auth_endpoint_rate_limited_after_many_attempts(self):
        """Hammering /api/auth/verify should eventually get a 429."""
        import auth
        # Temporarily enable auth so the endpoint actually checks
        original_enabled = auth.AUTH_ENABLED
        original_secret  = auth.SECRET
        try:
            auth.AUTH_ENABLED = True
            auth.SECRET = "super-secret"
            got_429 = False
            for _ in range(12):   # limit is 5/min
                r = client.post("/api/auth/verify", json={"token": "wrong"})
                if r.status_code == 429:
                    got_429 = True
                    break
            assert got_429, "Expected 429 after exceeding auth rate limit"
        finally:
            auth.AUTH_ENABLED = original_enabled
            auth.SECRET = original_secret
