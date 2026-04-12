#!/usr/bin/env python3
"""
vulntrix — Local AI-Powered Penetration Testing Assistant
============================================================

Usage (interactive REPL):
  python main.py

Usage (single command):
  python main.py recon /path/to/nmap.txt
  python main.py exploit reverse-shell --lhost 10.10.14.5

Usage (config overrides):
  python main.py --reasoning-model llama3 --coding-model codellama
  python main.py --ollama-url http://localhost:11434

Designed for use in lab / CTF environments (HTB, TryHackMe, DVWA, etc.)
All inference is fully local — no external API calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ─── make sure the project root is on PYTHONPATH ─────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config   import load_config, save_config
from ai_core  import ModelRouter
from ai_core.model_router import ModelConfig
from ai_core.ollama_client import OllamaClient, OllamaError
from cli      import PentestCLI


# ─── argument parser ─────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog        = "vulntrix",
        description = "Local AI penetration testing assistant (Ollama-powered)",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog      = __doc__,
    )
    p.add_argument(
        "command", nargs="?",
        help="Command to run non-interactively (e.g. 'recon', 'exploit')"
    )
    p.add_argument(
        "args", nargs=argparse.REMAINDER,
        help="Arguments for the command"
    )
    p.add_argument(
        "--reasoning-model", metavar="MODEL",
        help="Ollama model for analysis/reasoning (default: mistral)"
    )
    p.add_argument(
        "--coding-model", metavar="MODEL",
        help="Ollama model for code generation (default: deepseek-coder)"
    )
    p.add_argument(
        "--ollama-url", metavar="URL",
        help="Ollama API base URL (default: http://localhost:11434)"
    )
    p.add_argument(
        "--no-stream", action="store_true",
        help="Disable token streaming (wait for full response)"
    )
    p.add_argument(
        "--save-config", action="store_true",
        help="Persist CLI flag overrides to ~/.vulntrix/config.json"
    )
    p.add_argument(
        "--list-models", action="store_true",
        help="List available Ollama models and exit"
    )
    return p


# ─── startup checks ──────────────────────────────────────────────────────────

def check_ollama(url: str) -> None:
    client = OllamaClient(base_url=url)
    if not client.health_check():
        print(
            f"\n[ERROR] Cannot reach Ollama at {url}\n"
            "  • Make sure Ollama is running:  ollama serve\n"
            "  • Check the URL in ~/.vulntrix/config.json\n",
            file=sys.stderr,
        )
        sys.exit(1)


def check_models(client: OllamaClient, reasoning: str, coding: str) -> None:
    try:
        available = set(client.list_models())
    except OllamaError:
        return   # connectivity already checked above

    missing = []
    if reasoning not in available:
        missing.append((reasoning, "reasoning"))
    if coding not in available:
        missing.append((coding, "coding"))

    if missing:
        print("\n[WARNING] The following models are not yet pulled:")
        for model, role in missing:
            print(f"  {role:12} → {model}")
        print("\nPull them with:")
        for model, _ in missing:
            print(f"  ollama pull {model}")
        print(
            "\nThe assistant will still start — "
            "requests to missing models will fail at inference time.\n"
        )


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser  = build_parser()
    ns      = parser.parse_args()
    cfg     = load_config()

    # Apply CLI overrides
    if ns.reasoning_model:
        cfg.reasoning_model = ns.reasoning_model
    if ns.coding_model:
        cfg.coding_model = ns.coding_model
    if ns.ollama_url:
        cfg.ollama_url = ns.ollama_url
    if ns.no_stream:
        cfg.stream_output = False

    if ns.save_config:
        save_config(cfg)
        print(f"Config saved to ~/.vulntrix/config.json")

    # Ollama connectivity check
    check_ollama(cfg.ollama_url)

    # Build model router
    client = OllamaClient(base_url=cfg.ollama_url)
    model_cfg = ModelConfig(
        reasoning_model       = cfg.reasoning_model,
        coding_model          = cfg.coding_model,
        temperature_reasoning = cfg.temperature_reasoning,
        temperature_coding    = cfg.temperature_coding,
        max_tokens_reasoning  = cfg.max_tokens_reasoning,
        max_tokens_coding     = cfg.max_tokens_coding,
        stream                = cfg.stream_output,
    )
    router = ModelRouter(client=client, config=model_cfg)

    # --list-models flag
    if ns.list_models:
        try:
            models = client.list_models()
            print("Available Ollama models:")
            for m in sorted(models):
                reasoning_tag = " ← reasoning" if m == cfg.reasoning_model else ""
                coding_tag    = " ← coding"    if m == cfg.coding_model    else ""
                print(f"  {m}{reasoning_tag}{coding_tag}")
        except OllamaError as exc:
            print(f"Error listing models: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Warn about missing models (non-fatal)
    check_models(client, cfg.reasoning_model, cfg.coding_model)

    # Build CLI
    cli = PentestCLI(router=router)

    # Non-interactive single-command mode
    if ns.command:
        line = ns.command + (" " + " ".join(ns.args) if ns.args else "")
        result = cli.run_command(line)
        if result:
            print(result)
        return

    # Interactive REPL
    cli.run_interactive()


if __name__ == "__main__":
    main()
