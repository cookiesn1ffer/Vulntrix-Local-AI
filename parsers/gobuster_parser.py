"""
GobusterParser — extract discovered paths from gobuster / ffuf / dirsearch output.

Handles:
  - gobuster dir  output  (Status: 200 [...])
  - gobuster dns  output  (Found: subdomain.target.com)
  - ffuf JSON output
  - plain text lists (one URL / path per line)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WebPath:
    path        : str
    status_code : int   = 0
    size        : int   = 0
    redirect_to : str   = ""
    tool        : str   = "gobuster"

    @property
    def is_interesting(self) -> bool:
        """Return True for paths worth investigating."""
        interesting_codes = {200, 201, 204, 301, 302, 307, 401, 403, 500}
        interesting_paths = (
            "admin", "login", "config", "backup", "upload", "api",
            ".git", ".env", "phpinfo", "console", "manager",
            "dashboard", "secret", "token", "password", "credential",
            "wp-admin", "phpmyadmin", "actuator", "swagger",
        )
        if self.status_code not in interesting_codes:
            return False
        path_lower = self.path.lower()
        return any(p in path_lower for p in interesting_paths) or self.status_code in {200, 201}

    def __str__(self) -> str:
        parts = [f"[{self.status_code}]" if self.status_code else "", self.path]
        if self.size:
            parts.append(f"(size: {self.size})")
        if self.redirect_to:
            parts.append(f"→ {self.redirect_to}")
        return "  ".join(p for p in parts if p)


@dataclass
class GobusterResult:
    target_url: str             = ""
    tool       : str             = "gobuster"
    paths      : list[WebPath]  = field(default_factory=list)
    subdomains : list[str]      = field(default_factory=list)
    raw_text   : str             = ""

    @property
    def interesting_paths(self) -> list[WebPath]:
        return [p for p in self.paths if p.is_interesting]

    def summary(self) -> str:
        lines = [f"Target: {self.target_url}", f"Paths found: {len(self.paths)}"]
        if self.interesting_paths:
            lines.append("\nInteresting paths:")
            for p in self.interesting_paths:
                lines.append(f"  {p}")
        if self.subdomains:
            lines.append(f"\nSubdomains ({len(self.subdomains)}):")
            for s in self.subdomains[:20]:
                lines.append(f"  {s}")
        return "\n".join(lines)


class GobusterParser:
    """Parse gobuster / ffuf / dirsearch output."""

    @classmethod
    def from_file(cls, path: str | Path) -> GobusterResult:
        text = Path(path).read_text(errors="replace")
        return cls.from_string(text)

    @classmethod
    def from_string(cls, text: str) -> GobusterResult:
        # Try ffuf JSON first
        try:
            data = json.loads(text)
            if "results" in data:
                return cls._parse_ffuf_json(data, text)
        except (json.JSONDecodeError, KeyError):
            pass

        # Fall back to text-based detection
        if "gobuster" in text.lower() or "Status:" in text:
            return cls._parse_gobuster_text(text)
        if "FUZZ" in text or "ffuf" in text.lower():
            return cls._parse_ffuf_text(text)
        # dirsearch or generic
        return cls._parse_generic(text)

    # ─── format parsers ──────────────────────────────────────────────────────

    @classmethod
    def _parse_gobuster_text(cls, text: str) -> GobusterResult:
        result = GobusterResult(raw_text=text, tool="gobuster")

        # Extract target URL from header
        url_m = re.search(r"Url:\s+(\S+)", text)
        if url_m:
            result.target_url = url_m.group(1)

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("=") or line.startswith("["):
                continue

            # dir mode: /admin (Status: 200) [Size: 1234] [--> /admin/]
            dir_m = re.match(
                r"(/\S*)\s+\(Status:\s*(\d+)\)(?:\s+\[Size:\s*(\d+)\])?(?:\s+\[-->\s*(\S+)\])?",
                line,
            )
            if dir_m:
                result.paths.append(WebPath(
                    path        = dir_m.group(1),
                    status_code = int(dir_m.group(2)),
                    size        = int(dir_m.group(3)) if dir_m.group(3) else 0,
                    redirect_to = dir_m.group(4) or "",
                    tool        = "gobuster",
                ))
                continue

            # dns mode: Found: api.target.com
            dns_m = re.match(r"Found:\s+(\S+)", line)
            if dns_m:
                result.subdomains.append(dns_m.group(1))

        return result

    @classmethod
    def _parse_ffuf_json(cls, data: dict, raw: str) -> GobusterResult:
        result = GobusterResult(raw_text=raw, tool="ffuf")
        result.target_url = data.get("config", {}).get("url", "")
        for item in data.get("results", []):
            result.paths.append(WebPath(
                path        = item.get("input", {}).get("FUZZ", item.get("url", "")),
                status_code = item.get("status", 0),
                size        = item.get("length", 0),
                tool        = "ffuf",
            ))
        return result

    @classmethod
    def _parse_ffuf_text(cls, text: str) -> GobusterResult:
        result = GobusterResult(raw_text=text, tool="ffuf")
        for line in text.splitlines():
            # admin    [Status: 200, Size: 1234, ...]
            m = re.match(r"(\S+)\s+\[Status:\s*(\d+),\s*Size:\s*(\d+)", line.strip())
            if m:
                result.paths.append(WebPath(
                    path        = "/" + m.group(1).lstrip("/"),
                    status_code = int(m.group(2)),
                    size        = int(m.group(3)),
                    tool        = "ffuf",
                ))
        return result

    @classmethod
    def _parse_generic(cls, text: str) -> GobusterResult:
        """Fall-back: one path/URL per line, optionally with status codes."""
        result = GobusterResult(raw_text=text, tool="generic")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # try "200  /path" or "/path  200"
            m = re.match(r"(\d{3})\s+(/\S*)", line) or re.match(r"(/\S*)\s+(\d{3})", line)
            if m:
                groups = m.groups()
                if groups[0].isdigit():
                    code, path = int(groups[0]), groups[1]
                else:
                    path, code = groups[0], int(groups[1])
                result.paths.append(WebPath(path=path, status_code=code))
            elif line.startswith("/"):
                result.paths.append(WebPath(path=line))
        return result
