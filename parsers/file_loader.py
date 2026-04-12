"""
FileLoader — auto-detect tool type from file contents and dispatch to the
correct parser.

Detection heuristics (in order):
  1. File extension hints  (.xml → try nmap XML)
  2. Content fingerprints  (regex on first 50 lines)
  3. Fall back to GenericParser
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

from .nmap_parser     import NmapParser,     NmapResult
from .gobuster_parser import GobusterParser, GobusterResult
from .linpeas_parser  import LinpeasParser,  LinpeasResult
from .generic_parser  import GenericParser,  GenericResult

ScanResult = Union[NmapResult, GobusterResult, LinpeasResult, GenericResult]


# ─── fingerprint patterns ────────────────────────────────────────────────────
_NMAP_PATTERNS = [
    re.compile(r"Nmap scan report for"),
    re.compile(r"<nmaprun"),
    re.compile(r"# Nmap .* scan initiated"),
    re.compile(r"\d+/tcp\s+(open|filtered|closed)"),
]
_GOBUSTER_PATTERNS = [
    re.compile(r"Gobuster", re.I),
    re.compile(r"\(Status:\s*\d{3}\)"),
    re.compile(r"\[Status:\s*\d{3},"),   # ffuf
    re.compile(r'"results":\s*\['),       # ffuf JSON
]
_LINPEAS_PATTERNS = [
    re.compile(r"linpeas", re.I),
    re.compile(r"SUID.*files", re.I),
    re.compile(r"\[.*\+.*\].*interesting", re.I),
    re.compile(r"╔══════.*System Information", re.I),
]


def _detect_type(path: Path, text_sample: str) -> str:
    ext = path.suffix.lower()

    # XML → likely nmap
    if ext == ".xml" and re.search(r"<nmaprun", text_sample):
        return "nmap"

    for pat in _NMAP_PATTERNS:
        if pat.search(text_sample):
            return "nmap"

    for pat in _LINPEAS_PATTERNS:
        if pat.search(text_sample):
            return "linpeas"

    for pat in _GOBUSTER_PATTERNS:
        if pat.search(text_sample):
            return "gobuster"

    return "generic"


class FileLoader:
    """
    Load a scan output file, auto-detect its type, and return the
    appropriate parsed result object.
    """

    @classmethod
    def load(cls, path: str | Path) -> tuple[str, ScanResult]:
        """
        Returns ``(tool_type, result)`` where ``tool_type`` is one of
        "nmap", "gobuster", "linpeas", or "generic".
        """
        p    = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Scan file not found: {p}")

        text        = p.read_text(errors="replace")
        sample      = "\n".join(text.splitlines()[:50])
        tool_type   = _detect_type(p, sample)

        if tool_type == "nmap":
            return "nmap", NmapParser.from_string(text)
        if tool_type == "gobuster":
            return "gobuster", GobusterParser.from_string(text)
        if tool_type == "linpeas":
            return "linpeas", LinpeasParser.from_string(text)

        # generic fall-back
        result = GenericParser.from_file(p, tool_hint=p.stem)
        return "generic", result

    @classmethod
    def load_text(cls, text: str, tool_hint: str = "") -> tuple[str, ScanResult]:
        """
        Parse raw text (not a file).  Useful for piped input.
        """
        sample    = "\n".join(text.splitlines()[:50])
        tool_type = _detect_type(Path(tool_hint or "unknown"), sample)

        if tool_type == "nmap":
            return "nmap", NmapParser.from_string(text)
        if tool_type == "gobuster":
            return "gobuster", GobusterParser.from_string(text)
        if tool_type == "linpeas":
            return "linpeas", LinpeasParser.from_string(text)

        result = GenericParser.from_string(text, tool_hint=tool_hint)
        return "generic", result
