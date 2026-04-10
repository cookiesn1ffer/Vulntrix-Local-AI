#!/usr/bin/env python3
"""
my_recon.py — Standalone recon analysis script

Usage:
  python my_recon.py <target_ip> <scan_file>
  python my_recon.py 192.168.1.50 ./nmap_scan.txt
  python my_recon.py 10.10.10.1 ./gobuster.txt
"""

import sys
from pathlib import Path

from ai_core import ModelRouter
from ai_core.model_router import ModelConfig
from ai_core.ollama_client import OllamaClient
from prompts import ReconPrompts, SystemPrompts
from parsers import FileLoader
from context import TargetContext, SessionStore


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <target_ip> <scan_file>")
        sys.exit(1)

    target    = sys.argv[1]
    scan_file = sys.argv[2]

    # ── Load scan file ──────────────────────────────────────────────
    print(f"[*] Loading {scan_file}…")
    try:
        tool_type, result = FileLoader.load(scan_file)
        print(f"[+] Detected tool: {tool_type}")
    except FileNotFoundError:
        print(f"[-] File not found: {scan_file}")
        sys.exit(1)

    # ── Connect to Ollama ───────────────────────────────────────────
    print("[*] Connecting to Ollama…")
    try:
        client = OllamaClient()
        if not client.health_check():
            print("[-] Ollama is not running. Start it with: ollama serve")
            sys.exit(1)
    except Exception as e:
        print(f"[-] Ollama error: {e}")
        sys.exit(1)

    router = ModelRouter(client=client, config=ModelConfig())

    # ── Target context ──────────────────────────────────────────────
    print(f"[*] Setting up context for {target}…")
    ctx   = TargetContext(target)
    store = SessionStore()
    store.set_current(target)
    extra = ctx.get_all_analysis() or None

    # ── Build prompt — anti-hallucination path ─────────────────────
    print(f"[*] Analysing {tool_type} output…")

    if tool_type == "nmap":
        from parsers.nmap_parser import NmapResult
        if isinstance(result, NmapResult):
            nmap_res = result
            print(f"    Scan quality : {nmap_res.scan_quality.value}")
            print(f"    Open ports   : {len(nmap_res.open_ports)}")
            print(f"    Noise lines  : {nmap_res.metrics.noise_lines}")

            if nmap_res.has_reliable_data:
                ctx.set_metadata(
                    ip         = nmap_res.target,
                    hostname   = nmap_res.hostname,
                    os_guess   = nmap_res.os_guess,
                    open_ports = [p.port for p in nmap_res.open_ports],
                    services   = {str(p.port): p.service for p in nmap_res.open_ports},
                )
            else:
                print(f"[!] Low-quality scan — AI will recommend re-scanning")

            prompt = ReconPrompts.nmap_analysis(
                scan_output   = nmap_res.clean_text,
                target        = nmap_res.target or target,
                extra_context = extra,
                nmap_result   = nmap_res,
            )
        else:
            prompt = ReconPrompts.nmap_analysis(result.raw_text, target, extra)

    elif tool_type == "gobuster":
        from parsers.gobuster_parser import GobusterResult
        if isinstance(result, GobusterResult):
            ctx.set_metadata(ip=target)
        prompt = ReconPrompts.web_directory_analysis(
            result.raw_text, target, getattr(result, 'target_url', None), extra
        )

    elif tool_type == "linpeas":
        from parsers.linpeas_parser import LinpeasResult
        txt = result.top_sections_text() if isinstance(result, LinpeasResult) else result.raw_text
        usr = getattr(result, 'current_user', None)
        prompt = ReconPrompts.privesc_analysis(txt, target, usr, extra)

    else:
        prompt = ReconPrompts.generic_recon_analysis(tool_type, result.raw_text[:6000], target, extra)

    # ── Stream AI analysis ──────────────────────────────────────────
    print("\n[*] AI analysis:\n" + "─" * 70)
    parts = []
    for token in router.stream_analyse(prompt, system=SystemPrompts.REASONING):
        sys.stdout.write(token)
        sys.stdout.flush()
        parts.append(token)
    print("\n" + "─" * 70)

    # ── Save to context ─────────────────────────────────────────────
    ctx.save_analysis(tool_type, "".join(parts))
    ctx.save()
    print(f"\n[+] Saved to ~/.vulntrix/targets/{ctx.target_id}.json")


if __name__ == "__main__":
    main()
