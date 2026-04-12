"""
conftest.py — shared pytest fixtures for Vulntrix tests.

Fixtures
--------
tmp_data_dir   : a fresh temporary directory for TargetContext files (auto-cleanup)
sample_nmap    : raw text from sample_inputs/nmap_sample.txt
sample_gobuster: raw text from sample_inputs/gobuster_sample.txt
sample_linpeas : raw text from sample_inputs/linpeas_sample.txt
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── Make project root importable ─────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLES = PROJECT_ROOT / "sample_inputs"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Return a temp directory that TargetContext will use instead of ~/.vulntrix."""
    return tmp_path


@pytest.fixture()
def sample_nmap() -> str:
    return (SAMPLES / "nmap_sample.txt").read_text(encoding="utf-8")


@pytest.fixture()
def sample_gobuster() -> str:
    return (SAMPLES / "gobuster_sample.txt").read_text(encoding="utf-8")


@pytest.fixture()
def sample_linpeas() -> str:
    return (SAMPLES / "linpeas_sample.txt").read_text(encoding="utf-8")
