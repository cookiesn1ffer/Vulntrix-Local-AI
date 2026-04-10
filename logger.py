"""
logger.py — Structured rotating-file logger for Vulntrix.

Usage
-----
    from logger import get_logger
    log = get_logger(__name__)
    log.info("Server started on port %d", 8000)
    log.error("Ollama unreachable: %s", err)
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Log directory ─────────────────────────────────────────────────────────────
LOG_DIR  = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# ── Formatters ────────────────────────────────────────────────────────────────
_FMT      = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_formatter = logging.Formatter(_FMT, _DATE_FMT)

# ── Module-level registry so we don't add duplicate handlers ─────────────────
_configured: set[str] = set()


def get_logger(name: str = "vulntrix") -> logging.Logger:
    """
    Return a logger with:
      • Rotating file handler  → logs/app.log  (5 MB × 3 backups, DEBUG+)
      • Stream handler         → stdout         (INFO+)
    """
    logger = logging.getLogger(name)

    if name in _configured:
        return logger

    _configured.add(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Rotating file (keeps last 15 MB of logs across 3 files)
    fh = RotatingFileHandler(
        LOG_FILE,
        maxBytes    = 5_000_000,
        backupCount = 3,
        encoding    = "utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_formatter)

    # Console — INFO and above so the terminal stays readable
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


# ── Convenience instance used by other modules ────────────────────────────────
log = get_logger("vulntrix")
