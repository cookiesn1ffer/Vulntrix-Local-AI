"""
LinpeasParser — extract high-value findings from linpeas.sh / linenum output.

linpeas uses ANSI colour codes to indicate severity:
  Red/Bold  = Critical
  Yellow    = Interesting
  Green     = Informational

This parser strips ANSI codes, then heuristically groups findings by
section so the AI gets structured context rather than a wall of text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ─── ANSI escape code stripper ───────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ─── section header patterns seen in linpeas output ─────────────────────────
SECTION_PATTERNS = [
    (re.compile(r"═+\s*(.*?)\s*═+"),    "section"),
    (re.compile(r"╔+.*?╗"),             "section"),
    (re.compile(r"#####\s*(.+)"),        "section"),
    (re.compile(r"\[[\+\-\!]\]\s*(.+)"), "finding"),
]

# Sections we care most about for privesc
HIGH_VALUE_SECTIONS = {
    "suid", "sudo", "capabilities", "cron", "passwd", "shadow",
    "writable", "path", "env", "kernel", "network", "ssh",
    "docker", "lxd", "nfs", "password", "credential", "token",
    "history", "config", "backup",
}


@dataclass
class LinpeasFinding:
    severity   : str   = "info"     # critical / interesting / info
    section    : str   = ""
    content    : str   = ""

    def __str__(self) -> str:
        return f"[{self.severity.upper():10}] [{self.section}] {self.content[:120]}"


@dataclass
class LinpeasResult:
    target_host : str                  = ""
    current_user: str                  = ""
    kernel      : str                  = ""
    os_info     : str                  = ""
    findings    : list[LinpeasFinding] = field(default_factory=list)
    raw_text    : str                  = ""

    @property
    def critical_findings(self) -> list[LinpeasFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def interesting_findings(self) -> list[LinpeasFinding]:
        return [f for f in self.findings if f.severity == "interesting"]

    def summary(self) -> str:
        lines = []
        if self.current_user:
            lines.append(f"Current user : {self.current_user}")
        if self.kernel:
            lines.append(f"Kernel       : {self.kernel}")
        if self.os_info:
            lines.append(f"OS           : {self.os_info}")
        lines.append(f"\nCritical findings ({len(self.critical_findings)}):")
        for f in self.critical_findings[:20]:
            lines.append(f"  {f}")
        lines.append(f"\nInteresting findings ({len(self.interesting_findings)}):")
        for f in self.interesting_findings[:20]:
            lines.append(f"  {f}")
        return "\n".join(lines)

    def top_sections_text(self, max_chars: int = 6000) -> str:
        """
        Return a compact representation of high-value sections only,
        suitable for injecting into a prompt without overflowing the
        model's context window.
        """
        sections: dict[str, list[str]] = {}
        current = "general"
        for line in self.raw_text.splitlines():
            for pat, kind in SECTION_PATTERNS:
                m = pat.search(line)
                if m and kind == "section":
                    name = m.group(1).lower() if m.lastindex else line.lower()
                    current = name[:40]
                    break
            if any(kw in current for kw in HIGH_VALUE_SECTIONS):
                sections.setdefault(current, []).append(line)

        output_parts: list[str] = []
        for sec, lines in sections.items():
            block = "\n".join(lines[:60])
            output_parts.append(f"### {sec.upper()}\n{block}")
        full = "\n\n".join(output_parts)
        return full[:max_chars]


class LinpeasParser:
    """Parse linpeas.sh or linenum.sh output."""

    @classmethod
    def from_file(cls, path: str | Path) -> LinpeasResult:
        raw = Path(path).read_text(errors="replace")
        return cls.from_string(raw)

    @classmethod
    def from_string(cls, raw: str) -> LinpeasResult:
        clean = strip_ansi(raw)
        result = LinpeasResult(raw_text=clean)
        cls._extract_system_info(clean, result)
        cls._extract_findings(clean, result)
        return result

    # ─── private helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_system_info(text: str, result: LinpeasResult) -> None:
        for line in text.splitlines()[:100]:
            # Hostname
            if re.search(r"hostname|Host Name", line, re.I) and not result.target_host:
                m = re.search(r":\s*(\S+)", line)
                if m:
                    result.target_host = m.group(1)

            # Current user
            if re.search(r"Current user|whoami", line, re.I) and not result.current_user:
                m = re.search(r":\s*(\S+)", line)
                if m:
                    result.current_user = m.group(1)

            # Kernel
            if re.search(r"Kernel version|uname", line, re.I) and not result.kernel:
                m = re.search(r"Linux\s+\S+\s+([\d\.\-]+)", line)
                if m:
                    result.kernel = m.group(1)

            # OS
            if re.search(r"DISTRIB_DESCRIPTION|PRETTY_NAME", line) and not result.os_info:
                m = re.search(r'=\s*"?([^"\n]+)"?', line)
                if m:
                    result.os_info = m.group(1)

    @staticmethod
    def _extract_findings(text: str, result: LinpeasResult) -> None:
        current_section = "general"
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            # Detect section headers
            for pat, kind in SECTION_PATTERNS:
                if kind == "section" and pat.search(line):
                    m = pat.search(line)
                    current_section = (m.group(1) if m and m.lastindex else line).strip()[:50]
                    break

            # Heuristic severity: linpeas marks important lines with specific keywords
            severity = "info"
            lower = line.lower()

            critical_keywords = (
                "suid", "no passwd", "nopasswd", "writable passwd",
                "writable shadow", "sudo -l", "docker group", "lxd group",
                "writeable /etc/passwd", "writeable /etc/cron",
                "can write to /etc/passwd", "ptrace protection disabled",
                "kernel exploit",
            )
            interesting_keywords = (
                "password", "credential", "token", "secret", "key",
                "cron", "writable", "backup", ".ssh", "history",
                "config", "interesting", "possible", "vulnerable",
                "version", "outdated",
            )

            if any(kw in lower for kw in critical_keywords):
                severity = "critical"
            elif any(kw in lower for kw in interesting_keywords):
                severity = "interesting"

            if severity in ("critical", "interesting") and line.strip():
                result.findings.append(LinpeasFinding(
                    severity = severity,
                    section  = current_section,
                    content  = line.strip(),
                ))

            i += 1
