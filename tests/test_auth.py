"""
test_auth.py — unit tests for the session-based auth system.

Covers verify_token / verify_secret / create_session / refresh_session /
revoke_session with AUTH_ENABLED both on and off.
"""

from __future__ import annotations

import time
import pytest


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _patch_auth(monkeypatch, secret: str):
    """Enable auth with the given secret and clear the session store."""
    import auth
    monkeypatch.setattr(auth, "SECRET",       secret)
    monkeypatch.setattr(auth, "AUTH_ENABLED", bool(secret))
    # Clear any sessions left over from previous tests
    with auth._sessions_lock:
        auth._sessions.clear()


# ═══════════════════════════════════════════════════════════
# Auth DISABLED
# ═══════════════════════════════════════════════════════════

class TestAuthDisabled:

    @pytest.fixture(autouse=True)
    def _disable(self, monkeypatch):
        _patch_auth(monkeypatch, "")

    def test_auth_enabled_is_false(self):
        import auth
        assert auth.AUTH_ENABLED is False

    def test_verify_token_always_true(self):
        import auth
        assert auth.verify_token("") is True
        assert auth.verify_token("anything") is True
        assert auth.verify_token("wrong") is True

    def test_verify_secret_always_false_when_no_secret(self):
        import auth
        # verify_secret checks the raw BOT_SECRET — when empty it should be False
        assert auth.verify_secret("") is False
        assert auth.verify_secret("anything") is False


# ═══════════════════════════════════════════════════════════
# Auth ENABLED — verify_secret
# ═══════════════════════════════════════════════════════════

SECRET = "test-super-secret-key-9876"

class TestVerifySecret:

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        _patch_auth(monkeypatch, SECRET)

    def test_correct_secret(self):
        import auth
        assert auth.verify_secret(SECRET) is True

    def test_wrong_secret(self):
        import auth
        assert auth.verify_secret("wrong") is False

    def test_empty_secret(self):
        import auth
        assert auth.verify_secret("") is False

    def test_trailing_space(self):
        import auth
        assert auth.verify_secret(SECRET + " ") is False

    def test_truncated(self):
        import auth
        assert auth.verify_secret(SECRET[:-1]) is False


# ═══════════════════════════════════════════════════════════
# Session lifecycle
# ═══════════════════════════════════════════════════════════

class TestSessionLifecycle:

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        _patch_auth(monkeypatch, SECRET)

    def test_create_session_returns_string(self):
        import auth
        tok = auth.create_session("127.0.0.1")
        assert isinstance(tok, str) and len(tok) == 36  # UUID format

    def test_created_session_is_valid(self):
        import auth
        tok = auth.create_session()
        assert auth.verify_token(tok) is True

    def test_different_sessions_are_unique(self):
        import auth
        t1 = auth.create_session()
        t2 = auth.create_session()
        assert t1 != t2

    def test_unknown_token_is_invalid(self):
        import auth
        assert auth.verify_token("not-a-real-session-uuid") is False

    def test_empty_token_is_invalid(self):
        import auth
        assert auth.verify_token("") is False

    def test_revoke_session(self):
        import auth
        tok = auth.create_session()
        assert auth.verify_token(tok) is True
        auth.revoke_session(tok)
        assert auth.verify_token(tok) is False

    def test_revoke_nonexistent_is_safe(self):
        import auth
        auth.revoke_session("ghost-token")   # should not raise

    def test_refresh_extends_valid_session(self):
        import auth
        tok = auth.create_session()
        old_exp = auth.session_expires_at(tok)
        result  = auth.refresh_session(tok)
        new_exp = auth.session_expires_at(tok)
        assert result is True
        assert new_exp >= old_exp

    def test_refresh_nonexistent_returns_false(self):
        import auth
        assert auth.refresh_session("ghost") is False

    def test_refresh_revoked_returns_false(self):
        import auth
        tok = auth.create_session()
        auth.revoke_session(tok)
        assert auth.refresh_session(tok) is False


# ═══════════════════════════════════════════════════════════
# Session expiry
# ═══════════════════════════════════════════════════════════

class TestSessionExpiry:

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        _patch_auth(monkeypatch, SECRET)

    def test_expired_session_is_invalid(self, monkeypatch):
        import auth
        tok = auth.create_session()
        # Force expiry by backdating the session
        with auth._sessions_lock:
            auth._sessions[tok] = time.time() - 1
        assert auth.verify_token(tok) is False

    def test_purge_removes_expired(self, monkeypatch):
        import auth
        tok = auth.create_session()
        with auth._sessions_lock:
            auth._sessions[tok] = time.time() - 1
        auth._purge_expired()
        with auth._sessions_lock:
            assert tok not in auth._sessions


# ═══════════════════════════════════════════════════════════
# Integration: import auth module if fastapi is available
# ═══════════════════════════════════════════════════════════

def test_auth_module_api_surface():
    """Check that all expected public symbols exist."""
    pytest.importorskip("fastapi", reason="fastapi not installed")
    import auth
    for sym in ("AUTH_ENABLED", "SECRET", "SESSION_TTL",
                "verify_token", "verify_secret",
                "create_session", "refresh_session",
                "revoke_session", "session_expires_at",
                "auth_middleware"):
        assert hasattr(auth, sym), f"auth.{sym} missing"
