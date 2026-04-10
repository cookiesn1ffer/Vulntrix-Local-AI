"""
NmapParser — strict, anti-hallucination nmap output parser.

Architecture:
  1. PRE-FILTER  — strip all noise lines before any analysis
  2. VALIDATE    — only accept lines that EXPLICITLY state "open"
  3. SCORE       — compute scan quality (High/Medium/Low) from signal/noise ratio
  4. STRUCTURE   — return clean NmapResult with confidence metadata

Key rule: a port is ONLY accepted if the line matches:
  ^<port>/(tcp|udp)  open  <service>  [version]
  with state == "open" — never "filtered", never inferred.

Supports:
  - nmap normal text output   (nmap -oN / default)
  - nmap XML output           (nmap -oX)
  - nmap grepable output      (nmap -oG)
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class ScanQuality(Enum):
    HIGH   = "High"
    MEDIUM = "Medium"
    LOW    = "Low"
    EMPTY  = "Empty"      # scan produced zero useful output


class Confidence(Enum):
    HIGH   = "High"       # explicit open + version banner
    MEDIUM = "Medium"     # explicit open, no version
    LOW    = "Low"        # open but scan had quality issues


# ── Noise patterns ─────────────────────────────────────────────────────────────
# Lines matching ANY of these are silently discarded before parsing.

_NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^adjust_timeouts2:"),
    re.compile(r"^NSOCK"),
    re.compile(r"^Increasing send delay"),
    re.compile(r"^Warning:.*giving up"),
    re.compile(r"^Read from"),
    re.compile(r"Timing out"),
    re.compile(r"^pcap_open_live"),
    re.compile(r"^Packet capture filter"),
    re.compile(r"^Initiating (SYN|Connect|UDP|SCTP|ARP|Ping) Scan"),
    re.compile(r"^Initiating Service"),
    re.compile(r"^Initiating OS detection"),
    re.compile(r"^Scanning \d+ host"),
    re.compile(r"^Completed (SYN|Connect|Service|NSE|OS)"),
    re.compile(r"^NSE:"),
    re.compile(r"^Overall sending"),
    re.compile(r"^Stats:"),
    re.compile(r"^SYN Stealth Scan"),
    re.compile(r"^Host is up"),
    re.compile(r"^Service detection performed"),
    re.compile(r"^Nmap done:"),
]

# The ONLY pattern that proves a port is open (strict):
#   80/tcp   open   http   Apache httpd 2.4.49
_OPEN_PORT_RE = re.compile(
    r"^(\d{1,5})/(tcp|udp)\s+open\s+(\S+)?\s*(.*)",
    re.IGNORECASE,
)

# Filtered/closed — tracked for context but never treated as open
_NONOPEN_PORT_RE = re.compile(
    r"^(\d{1,5})/(tcp|udp)\s+(filtered|closed)\s*(\S+)?\s*(.*)",
    re.IGNORECASE,
)


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class PortInfo:
    port:        int
    protocol:    str        = "tcp"
    state:       str        = "open"
    service:     str        = ""
    version:     str        = ""
    extra_info:  str        = ""
    confidence:  Confidence = Confidence.HIGH

    def __str__(self) -> str:
        ver = f" {self.version}" if self.version else ""
        conf = f" [{self.confidence.value} confidence]"
        return f"{self.port}/{self.protocol}  open  {self.service}{ver}{conf}"

    def to_dict(self) -> dict:
        return {
            "port":       self.port,
            "protocol":   self.protocol,
            "state":      self.state,
            "service":    self.service or "unknown",
            "version":    self.version or "",
            "confidence": self.confidence.value,
        }


@dataclass
class ScanMetrics:
    """Quantitative quality metrics extracted during parsing."""
    total_lines:         int = 0
    noise_lines:         int = 0
    signal_lines:        int = 0    # meaningful non-noise lines
    open_port_lines:     int = 0
    has_version_scan:    bool = False
    has_os_detection:    bool = False
    timeout_count:       int = 0
    scan_flags:          str = ""

    @property
    def noise_ratio(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return self.noise_lines / self.total_lines

    @property
    def quality(self) -> ScanQuality:
        if self.open_port_lines == 0 and self.signal_lines <= 3:
            return ScanQuality.EMPTY
        if self.noise_ratio > 0.5 or self.timeout_count > 10:
            return ScanQuality.LOW
        if self.noise_ratio > 0.2 or not self.has_version_scan:
            return ScanQuality.MEDIUM
        return ScanQuality.HIGH

    @property
    def quality_reason(self) -> str:
        reasons = []
        if self.timeout_count > 0:
            reasons.append(f"{self.timeout_count} timeout events (adjust_timeouts2 spam — suggests slow/filtered host)")
        if self.noise_ratio > 0.2:
            reasons.append(f"{self.noise_ratio:.0%} of output is noise/debug lines")
        if not self.has_version_scan:
            reasons.append("no -sV service version detection (versions may be guessed, not confirmed)")
        if self.open_port_lines == 0:
            reasons.append("zero open ports detected in scan output")
        if not reasons:
            return "clean scan output with version detection"
        return "; ".join(reasons)


@dataclass
class NmapResult:
    target:           str               = ""
    hostname:         str               = ""
    os_guess:         str               = ""
    open_ports:       list[PortInfo]    = field(default_factory=list)
    filtered_ports:   list[PortInfo]    = field(default_factory=list)
    ignored_data:     list[str]         = field(default_factory=list)
    metrics:          ScanMetrics       = field(default_factory=ScanMetrics)
    raw_text:         str               = ""
    clean_text:       str               = ""    # raw with noise stripped
    source_format:    str               = "unknown"

    # ── convenience ───────────────────────────────────────────────────────────

    @property
    def scan_quality(self) -> ScanQuality:
        return self.metrics.quality

    @property
    def has_reliable_data(self) -> bool:
        return (
            self.scan_quality in (ScanQuality.HIGH, ScanQuality.MEDIUM)
            and len(self.open_ports) > 0
        )

    def summary(self) -> str:
        lines = []
        lines.append(f"Target      : {self.target or 'unknown'}")
        if self.hostname:
            lines.append(f"Hostname    : {self.hostname}")
        if self.os_guess:
            lines.append(f"OS          : {self.os_guess}")
        lines.append(f"Scan Quality: {self.scan_quality.value} — {self.metrics.quality_reason}")
        lines.append(f"Open Ports  : {len(self.open_ports)}")
        for p in self.open_ports:
            lines.append(f"  {p}")
        if self.filtered_ports:
            lines.append(f"Filtered    : {len(self.filtered_ports)} ports")
        if self.ignored_data:
            lines.append(f"\nIgnored ({len(self.ignored_data)} entries):")
            for ig in self.ignored_data[:5]:
                lines.append(f"  {ig}")
            if len(self.ignored_data) > 5:
                lines.append(f"  ... and {len(self.ignored_data)-5} more")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "target":         self.target,
            "hostname":       self.hostname,
            "os_guess":       self.os_guess,
            "scan_quality":   self.scan_quality.value,
            "quality_reason": self.metrics.quality_reason,
            "open_ports":     [p.to_dict() for p in self.open_ports],
            "filtered_count": len(self.filtered_ports),
            "ignored_count":  len(self.ignored_data),
            "has_reliable_data": self.has_reliable_data,
        }


# ── Parser ─────────────────────────────────────────────────────────────────────

class NmapParser:
    """
    Strict nmap output parser with noise rejection and quality scoring.

    Design principles:
      - Parse then validate, NEVER infer
      - Every accepted port must match _OPEN_PORT_RE
      - Noise is counted, not interpreted
      - Scan quality gates downstream AI analysis
    """

    # ── public API ────────────────────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | Path) -> NmapResult:
        text = Path(path).read_text(errors="replace")
        return cls.from_string(text)

    @classmethod
    def from_string(cls, text: str) -> NmapResult:
        stripped = text.strip()
        if stripped.startswith("<?xml") or stripped.startswith("<nmaprun"):
            return cls._parse_xml(stripped)
        if stripped.startswith("# Nmap") and re.search(r"^Host:\s+\S+", stripped, re.M):
            return cls._parse_grepable(stripped)
        return cls._parse_text(stripped)

    # ── noise filter ──────────────────────────────────────────────────────────

    @classmethod
    def _strip_noise(cls, text: str) -> tuple[str, ScanMetrics]:
        """
        Remove all noise lines. Return (clean_text, metrics).
        This is the FIRST step — all parsers call this before any extraction.
        """
        metrics = ScanMetrics()
        clean_lines = []
        ignored_reasons = []

        for line in text.splitlines():
            metrics.total_lines += 1
            raw = line.strip()

            if not raw:
                continue

            # Check noise patterns
            is_noise = any(pat.search(raw) for pat in _NOISE_PATTERNS)

            # Special: count adjust_timeouts2 occurrences
            if raw.startswith("adjust_timeouts2:"):
                metrics.timeout_count += 1
                metrics.noise_lines   += 1
                continue

            if is_noise:
                metrics.noise_lines += 1
                continue

            # Check for scan flags in header
            if raw.startswith("# Nmap") or "Nmap scan report" in raw:
                m = re.search(r"as:\s+(.+)", raw)
                if m:
                    metrics.scan_flags = m.group(1).strip()
                if "-sV" in metrics.scan_flags or "--version" in metrics.scan_flags:
                    metrics.has_version_scan = True
                if "-O" in metrics.scan_flags or "--osscan" in metrics.scan_flags:
                    metrics.has_os_detection = True

            clean_lines.append(line)
            metrics.signal_lines += 1

        clean_text = "\n".join(clean_lines)
        return clean_text, metrics

    # ── format parsers ────────────────────────────────────────────────────────

    @classmethod
    def _parse_text(cls, text: str) -> NmapResult:
        clean_text, metrics = cls._strip_noise(text)
        result = NmapResult(raw_text=text, clean_text=clean_text,
                            metrics=metrics, source_format="text")

        for line in clean_text.splitlines():
            raw = line.strip()
            if not raw:
                continue

            # Target
            m = re.match(r"Nmap scan report for\s+(?:(\S+)\s+\()?(\S+?)\)?$", raw)
            if m:
                if m.group(1):
                    result.hostname = m.group(1)
                    result.target   = m.group(2).strip("()")
                else:
                    result.target = m.group(2)
                continue

            # STRICT: open port — must literally say "open"
            pm = _OPEN_PORT_RE.match(raw)
            if pm:
                service = (pm.group(3) or "").strip()
                version = (pm.group(4) or "").strip()

                # Skip false service names that nmap uses for unknown ports
                if service in ("unknown", ""):
                    service = ""

                conf = Confidence.HIGH if version else Confidence.MEDIUM
                if metrics.quality == ScanQuality.LOW:
                    conf = Confidence.LOW

                info = PortInfo(
                    port=int(pm.group(1)),
                    protocol=pm.group(2).lower(),
                    state="open",
                    service=service,
                    version=version,
                    confidence=conf,
                )
                result.open_ports.append(info)
                metrics.open_port_lines += 1
                continue

            # Filtered/closed — track but never treat as open
            nm = _NONOPEN_PORT_RE.match(raw)
            if nm:
                info = PortInfo(
                    port=int(nm.group(1)),
                    protocol=nm.group(2).lower(),
                    state=nm.group(3).lower(),
                    service=(nm.group(4) or "").strip(),
                )
                result.filtered_ports.append(info)
                result.ignored_data.append(
                    f"Port {nm.group(1)}/{nm.group(2)} is {nm.group(3)} — excluded from attack surface"
                )
                continue

            # OS detection
            os_m = re.match(r"OS details:\s+(.+)", raw)
            if os_m:
                result.os_guess = os_m.group(1).strip()
                continue

            ag_m = re.match(r"Aggressive OS guesses:\s+(.+)", raw)
            if ag_m and not result.os_guess:
                result.os_guess = ag_m.group(1).split(",")[0].strip()
                continue

            # "Not shown: N closed|filtered ports"
            ns_m = re.match(r"Not shown:\s+(\d+)\s+(closed|filtered)", raw)
            if ns_m:
                result.ignored_data.append(
                    f"{ns_m.group(1)} {ns_m.group(2)} ports not shown (nmap suppressed as expected)"
                )
                continue

        # Extract target from scan flags if not found in output
        if not result.target and metrics.scan_flags:
            m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}(?:/\d+)?)", metrics.scan_flags)
            if m:
                result.target = m.group(1)

        return result

    @classmethod
    def _parse_xml(cls, xml_text: str) -> NmapResult:
        clean_text, metrics = cls._strip_noise(xml_text)
        result = NmapResult(raw_text=xml_text, clean_text=clean_text,
                            metrics=metrics, source_format="xml")
        try:
            root = ET.fromstring(xml_text)
            args = root.attrib.get("args", "")
            metrics.scan_flags = args
            metrics.has_version_scan = "-sV" in args
            metrics.has_os_detection = "-O" in args

            for host in root.findall(".//host"):
                addr_el = host.find("address[@addrtype='ipv4']")
                if addr_el is not None:
                    result.target = addr_el.attrib.get("addr", "")

                hn = host.find(".//hostname")
                if hn is not None:
                    result.hostname = hn.attrib.get("name", "")

                osmatch = host.find(".//osmatch")
                if osmatch is not None:
                    result.os_guess = osmatch.attrib.get("name", "")

                for port_el in host.findall(".//port"):
                    state_el   = port_el.find("state")
                    service_el = port_el.find("service")
                    state = state_el.attrib.get("state", "") if state_el is not None else ""

                    # STRICT: only "open"
                    if state != "open":
                        if state in ("filtered", "closed"):
                            port_num = port_el.attrib.get("portid", "?")
                            proto    = port_el.attrib.get("protocol", "tcp")
                            result.ignored_data.append(
                                f"Port {port_num}/{proto} is {state} — excluded"
                            )
                            result.filtered_ports.append(PortInfo(
                                port=int(port_el.attrib.get("portid", 0)),
                                protocol=proto, state=state,
                            ))
                        continue

                    service = ""
                    version = ""
                    if service_el is not None:
                        service = service_el.attrib.get("name", "")
                        version = " ".join(filter(None, [
                            service_el.attrib.get("product", ""),
                            service_el.attrib.get("version", ""),
                            service_el.attrib.get("extrainfo", ""),
                        ]))

                    conf = Confidence.HIGH if version else Confidence.MEDIUM
                    info = PortInfo(
                        port=int(port_el.attrib.get("portid", 0)),
                        protocol=port_el.attrib.get("protocol", "tcp"),
                        state="open",
                        service=service,
                        version=version.strip(),
                        confidence=conf,
                    )
                    result.open_ports.append(info)
                    metrics.open_port_lines += 1
                break  # first host only

        except ET.ParseError as exc:
            result.ignored_data.append(f"XML parse error: {exc}")

        return result

    @classmethod
    def _parse_grepable(cls, text: str) -> NmapResult:
        clean_text, metrics = cls._strip_noise(text)
        result = NmapResult(raw_text=text, clean_text=clean_text,
                            metrics=metrics, source_format="grepable")

        for line in clean_text.splitlines():
            raw = line.strip()
            if raw.startswith("#") or not raw:
                continue

            host_m = re.match(r"Host:\s+(\S+)\s*(?:\(([^)]*)\))?", raw)
            if host_m:
                result.target   = host_m.group(1)
                result.hostname = host_m.group(2) or ""

            ports_m = re.search(r"Ports:\s+(.+?)(?:\s*Ignored|$)", raw)
            if ports_m:
                for entry in ports_m.group(1).split(","):
                    entry = entry.strip()
                    parts = entry.split("/")
                    if len(parts) >= 3:
                        state = parts[1].lower() if len(parts) > 1 else ""
                        if state != "open":
                            if state in ("filtered", "closed"):
                                result.ignored_data.append(
                                    f"Port {parts[0]}/{parts[2]} is {state} — excluded"
                                )
                            continue
                        service = parts[4] if len(parts) > 4 else ""
                        version = parts[6] if len(parts) > 6 else ""
                        info = PortInfo(
                            port=int(parts[0]),
                            state="open",
                            protocol=parts[2],
                            service=service,
                            version=version,
                            confidence=Confidence.HIGH if version else Confidence.MEDIUM,
                        )
                        result.open_ports.append(info)
                        metrics.open_port_lines += 1

            # Ignored ports line
            ig_m = re.search(r"Ignored State: (\w+) \((\d+)\)", raw)
            if ig_m:
                result.ignored_data.append(
                    f"{ig_m.group(2)} ports in state '{ig_m.group(1)}' suppressed by nmap"
                )

        # Extract target from scan comment if missing
        if not result.target:
            for line in text.splitlines():
                m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                if m:
                    result.target = m.group(1)
                    break

        return result


# ── Convenience function ───────────────────────────────────────────────────────

def parse_nmap(text_or_path: str | Path) -> NmapResult:
    """Parse nmap output from a file path or raw string."""
    p = Path(text_or_path)
    if p.exists():
        return NmapParser.from_file(p)
    return NmapParser.from_string(str(text_or_path))
