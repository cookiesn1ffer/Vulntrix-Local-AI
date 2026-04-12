"""
config.py — runtime configuration for vulntrix.

Values can be overridden via:
  1. ~/.vulntrix/config.json  (user persistent config)
  2. Environment variables      (VULNTRIX_*)
  3. CLI flags                  (highest priority, parsed in main.py)
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


CONFIG_PATH = Path.home() / ".vulntrix" / "config.json"


@dataclass
class Config:
    # Ollama
    ollama_url           : str   = "http://localhost:11434"

    # Models
    reasoning_model      : str   = "mistral"
    coding_model         : str   = "deepseek-coder"

    # Generation parameters
    temperature_reasoning: float = 0.6
    temperature_coding   : float = 0.3
    max_tokens_reasoning : int   = 4096
    max_tokens_coding    : int   = 8192

    # UX
    stream_output        : bool  = True
    color_output         : bool  = True

    # Attacker defaults (override per-session)
    default_lhost        : str   = "10.10.14.1"
    default_lport        : int   = 4444


def load_config(config_path: Optional[Path] = None) -> Config:
    """
    Load config from disk (if exists), then overlay environment variables.
    Returns a Config dataclass.
    """
    path = config_path or CONFIG_PATH
    cfg  = Config()

    # Load from JSON if present
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        except (json.JSONDecodeError, OSError):
            pass   # silently ignore corrupt config

    # Overlay environment variables
    ENV_MAP = {
        "VULNTRIX_OLLAMA_URL"      : ("ollama_url",            str),
        "VULNTRIX_REASONING_MODEL" : ("reasoning_model",       str),
        "VULNTRIX_CODING_MODEL"    : ("coding_model",          str),
        "VULNTRIX_LHOST"           : ("default_lhost",         str),
        "VULNTRIX_LPORT"           : ("default_lport",         int),
        "VULNTRIX_STREAM"          : ("stream_output",         lambda v: v.lower() in ("1","true","yes")),
    }
    for env_var, (attr, cast) in ENV_MAP.items():
        val = os.environ.get(env_var)
        if val is not None:
            try:
                setattr(cfg, attr, cast(val))
            except (ValueError, TypeError):
                pass

    return cfg


def save_config(cfg: Config, config_path: Optional[Path] = None) -> None:
    path = config_path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
