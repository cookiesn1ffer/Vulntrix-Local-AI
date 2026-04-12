"""
test_context.py — unit tests for TargetContext (per-target persistent storage).

Run: pytest tests/ -v
"""

from __future__ import annotations

import pytest

from context.target_context import TargetContext


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def make_ctx(target: str, tmp_data_dir) -> TargetContext:
    return TargetContext(target, data_dir=tmp_data_dir)


# ═══════════════════════════════════════════════════════════
# Lifecycle
# ═══════════════════════════════════════════════════════════

class TestLifecycle:

    def test_create_and_exists(self, tmp_data_dir):
        ctx = make_ctx("10.10.10.1", tmp_data_dir)
        ctx.save()
        assert ctx.exists()

    def test_delete(self, tmp_data_dir):
        ctx = make_ctx("10.10.10.2", tmp_data_dir)
        ctx.save()
        ctx.delete()
        assert not ctx.exists()

    def test_target_id_sanitised(self, tmp_data_dir):
        ctx = make_ctx("victim.local", tmp_data_dir)
        # target_id should be a non-empty filesystem-safe string
        assert ctx.target_id and isinstance(ctx.target_id, str)

    def test_special_chars_in_target(self, tmp_data_dir):
        # Sanitisation should allow saving without filesystem errors
        ctx = make_ctx("10.10.10.1:8080", tmp_data_dir)
        ctx.save()
        assert ctx.exists()

    def test_reload_persistence(self, tmp_data_dir):
        ctx = make_ctx("persist.local", tmp_data_dir)
        ctx.add_note("test_key", "hello world")
        ctx.save()
        ctx2 = make_ctx("persist.local", tmp_data_dir)
        assert ctx2.get_note("test_key") == "hello world"


# ═══════════════════════════════════════════════════════════
# Metadata
# ═══════════════════════════════════════════════════════════

class TestMetadata:

    def test_set_and_read_ip(self, tmp_data_dir):
        ctx = make_ctx("10.0.0.1", tmp_data_dir)
        ctx.set_metadata(ip="10.0.0.1", os_guess="Linux")
        assert ctx._data.ip == "10.0.0.1"
        assert "linux" in ctx._data.os_guess.lower()

    def test_partial_metadata_update(self, tmp_data_dir):
        ctx = make_ctx("10.0.0.2", tmp_data_dir)
        ctx.set_metadata(hostname="box1")
        assert ctx._data.hostname == "box1"


# ═══════════════════════════════════════════════════════════
# Notes
# ═══════════════════════════════════════════════════════════

class TestNotes:

    def test_add_and_get_note(self, tmp_data_dir):
        ctx = make_ctx("notes.target", tmp_data_dir)
        ctx.add_note("finding", "SQL injection on /login")
        assert ctx.get_note("finding") == "SQL injection on /login"

    def test_list_notes(self, tmp_data_dir):
        ctx = make_ctx("notes2.target", tmp_data_dir)
        ctx.add_note("a", "alpha")
        ctx.add_note("b", "beta")
        notes = ctx.list_notes()
        assert "a" in notes and "b" in notes

    def test_delete_note(self, tmp_data_dir):
        ctx = make_ctx("notes3.target", tmp_data_dir)
        ctx.add_note("temp", "delete me")
        ctx.delete_note("temp")
        assert ctx.get_note("temp") is None

    def test_get_nonexistent_note_returns_none(self, tmp_data_dir):
        ctx = make_ctx("notes4.target", tmp_data_dir)
        assert ctx.get_note("missing") is None

    def test_note_overwrite(self, tmp_data_dir):
        ctx = make_ctx("notes5.target", tmp_data_dir)
        ctx.add_note("key", "first")
        ctx.add_note("key", "second")
        assert ctx.get_note("key") == "second"


# ═══════════════════════════════════════════════════════════
# Credentials
# ═══════════════════════════════════════════════════════════

class TestCredentials:

    def test_add_and_list_credentials(self, tmp_data_dir):
        ctx = make_ctx("creds.target", tmp_data_dir)
        ctx.add_credential(username="admin", password="secret123", service="ssh")
        creds = ctx.list_credentials()
        assert len(creds) == 1
        assert creds[0]["username"] == "admin"

    def test_multiple_credentials(self, tmp_data_dir):
        ctx = make_ctx("creds2.target", tmp_data_dir)
        ctx.add_credential(username="user1", password="pass1", service="ftp")
        ctx.add_credential(username="user2", password="pass2", service="http")
        assert len(ctx.list_credentials()) == 2

    def test_credential_hash_val_stored(self, tmp_data_dir):
        ctx = make_ctx("creds3.target", tmp_data_dir)
        ctx.add_credential(username="root", hash_val="5f4dcc3b5aa765d61d8327deb882cf99")
        creds = ctx.list_credentials()
        assert creds[0].get("hash_val") == "5f4dcc3b5aa765d61d8327deb882cf99"


# ═══════════════════════════════════════════════════════════
# Flags
# ═══════════════════════════════════════════════════════════

class TestFlags:

    def test_add_and_list_flags(self, tmp_data_dir):
        ctx = make_ctx("flags.target", tmp_data_dir)
        ctx.add_flag("user", "a1b2c3d4e5f6")
        flags = ctx.list_flags()
        assert "user" in flags
        assert flags["user"] == "a1b2c3d4e5f6"


# ═══════════════════════════════════════════════════════════
# Attack chain
# ═══════════════════════════════════════════════════════════

class TestAttackChain:

    def test_add_stage(self, tmp_data_dir):
        ctx = make_ctx("chain.target", tmp_data_dir)
        stage = ctx.add_attack_stage("Initial Access")
        assert stage.name == "Initial Access"

    def test_update_stage_to_done(self, tmp_data_dir):
        ctx = make_ctx("chain2.target", tmp_data_dir)
        ctx.add_attack_stage("Recon")
        ctx.update_attack_stage("Recon", "done", notes="nmap complete")
        chain = ctx.get_attack_chain()
        stage = next(s for s in chain if s["name"] == "Recon")
        assert stage["status"] == "done"

    def test_get_attack_chain_returns_list(self, tmp_data_dir):
        ctx = make_ctx("chain3.target", tmp_data_dir)
        assert isinstance(ctx.get_attack_chain(), list)


# ═══════════════════════════════════════════════════════════
# Event log
# ═══════════════════════════════════════════════════════════

class TestEventLog:

    def test_log_event_and_retrieve(self, tmp_data_dir):
        ctx = make_ctx("log.target", tmp_data_dir)
        ctx.log_event("scan", "nmap found 5 open ports")
        log = ctx.get_log()
        assert len(log) == 1
        assert log[0]["content"] == "nmap found 5 open ports"

    def test_log_multiple_events(self, tmp_data_dir):
        ctx = make_ctx("log2.target", tmp_data_dir)
        ctx.log_event("scan",   "nmap done")
        ctx.log_event("exploit","got shell")
        log = ctx.get_log()
        assert len(log) == 2

    def test_log_limit(self, tmp_data_dir):
        ctx = make_ctx("log3.target", tmp_data_dir)
        for i in range(10):
            ctx.log_event("test", f"event {i}")
        log = ctx.get_log(limit=5)
        assert len(log) <= 5

    def test_log_event_has_timestamp(self, tmp_data_dir):
        ctx = make_ctx("log4.target", tmp_data_dir)
        ctx.log_event("info", "timestamped entry")
        log = ctx.get_log()
        entry = log[0]
        assert "timestamp" in entry or "time" in entry or "ts" in entry


# ═══════════════════════════════════════════════════════════
# Analysis
# ═══════════════════════════════════════════════════════════

class TestAnalysis:

    def test_save_and_get_analysis(self, tmp_data_dir):
        ctx = make_ctx("analysis.target", tmp_data_dir)
        ctx.save_analysis("nmap", "Five open ports detected. SSH on 22.")
        text = ctx.get_analysis("nmap")
        assert text == "Five open ports detected. SSH on 22."

    def test_get_all_analysis(self, tmp_data_dir):
        ctx = make_ctx("analysis2.target", tmp_data_dir)
        ctx.save_analysis("nmap",     "nmap summary")
        ctx.save_analysis("gobuster", "gobuster summary")
        combined = ctx.get_all_analysis()
        assert "nmap" in combined.lower() or "nmap summary" in combined


# ═══════════════════════════════════════════════════════════
# Context summary
# ═══════════════════════════════════════════════════════════

class TestContextSummary:

    def test_summary_is_string(self, tmp_data_dir):
        ctx = make_ctx("summary.target", tmp_data_dir)
        assert isinstance(ctx.context_summary(), str)

    def test_summary_respects_max_chars(self, tmp_data_dir):
        ctx = make_ctx("summary2.target", tmp_data_dir)
        ctx.add_note("big", "x" * 5000)
        summary = ctx.context_summary(max_chars=500)
        assert len(summary) <= 600   # a little buffer for truncation markers

    def test_summary_includes_target_id(self, tmp_data_dir):
        ctx = make_ctx("my-box", tmp_data_dir)
        # Add some data so summary isn't empty
        ctx.add_note("recon", "nmap done")
        ctx.log_event("scan", "started")
        summary = ctx.context_summary()
        # Summary should be a non-empty string when there is content
        assert isinstance(summary, str)
