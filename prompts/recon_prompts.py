"""
Prompt templates for reconnaissance analysis.

Anti-hallucination architecture:
  - The AI NEVER receives raw noisy scan text
  - Only pre-validated, structured port data is sent
  - Hard constraints are baked into every prompt
  - Empty/low-confidence scans are handled BEFORE calling the AI
  - Output format is strictly prescribed to prevent fabrication
"""

from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from parsers.nmap_parser import NmapResult, ScanQuality


# ── Anti-hallucination system prompt ──────────────────────────────────────────
# Injected into EVERY recon analysis call.

ANTI_HALLUCINATION_RULES = """
CRITICAL RULES — VIOLATION IS UNACCEPTABLE:
1. ONLY analyse ports, services, and versions that are EXPLICITLY listed in the VERIFIED PORTS section below.
2. NEVER infer, assume, or mention any service not present in the verified data.
3. NEVER suggest attacks on ports that are not in the verified list.
4. If the scan quality is LOW, prefix every finding with a caveat about confidence.
5. If the verified port list is EMPTY, output ONLY the phrase:
   "Insufficient reliable data. Further enumeration required."
   Then stop. Do not speculate or fill space.
6. Do not use phrases like "typically", "usually", or "might be" to introduce services that aren't confirmed.
7. Every attack suggestion MUST reference a specific port from the verified list.
""".strip()


class ReconPrompts:
    """Build prompts for reconnaissance-phase analysis."""

    # ── nmap — main entry point ───────────────────────────────────────────────

    @staticmethod
    def nmap_analysis(
        scan_output: str,
        target: str,
        extra_context: Optional[str] = None,
        nmap_result: Optional["NmapResult"] = None,
    ) -> str:
        """
        Build an nmap analysis prompt.

        If nmap_result is provided (preferred), uses pre-validated structured data.
        Falls back to raw scan_output only when nmap_result is absent.
        """
        if nmap_result is not None:
            return ReconPrompts._nmap_from_result(nmap_result, target, extra_context)
        # Legacy path — still filter raw text before sending
        return ReconPrompts._nmap_from_raw(scan_output, target, extra_context)

    @staticmethod
    def _nmap_from_result(
        result: "NmapResult",
        target: str,
        extra_context: Optional[str] = None,
    ) -> str:
        """Build prompt from a validated NmapResult object (preferred path)."""
        from parsers.nmap_parser import ScanQuality

        quality = result.scan_quality
        quality_str  = quality.value
        quality_reason = result.metrics.quality_reason

        # Build the verified ports block — ONLY confirmed open ports
        if result.open_ports:
            ports_block = "\n".join(
                f"  • Port {p.port}/{p.protocol}  |  Service: {p.service or 'unknown'}"
                f"  |  Version: {p.version or 'not detected'}"
                f"  |  Confidence: {p.confidence.value}"
                for p in result.open_ports
            )
        else:
            ports_block = "  NONE — zero open ports confirmed in this scan"

        # Ignored data block
        if result.ignored_data:
            ignored_block = "\n".join(f"  - {ig}" for ig in result.ignored_data[:10])
        else:
            ignored_block = "  None"

        # Metrics block
        metrics = result.metrics
        metrics_block = (
            f"  Total lines in file : {metrics.total_lines}\n"
            f"  Noise lines stripped: {metrics.noise_lines} "
            f"({metrics.noise_ratio:.0%} of output)\n"
            f"  Timeout events      : {metrics.timeout_count}\n"
            f"  Version scan (-sV)  : {'Yes' if metrics.has_version_scan else 'No'}\n"
            f"  OS detection (-O)   : {'Yes' if metrics.has_os_detection else 'No'}\n"
            f"  Scan flags used     : {metrics.scan_flags or 'unknown'}"
        )

        ctx_block = (
            f"\n## Previous Recon Context\n{extra_context}\n"
            if extra_context else ""
        )

        # Early-exit template for empty/unreliable scans
        if quality == ScanQuality.EMPTY or not result.open_ports:
            return f"""{ANTI_HALLUCINATION_RULES}

# Nmap Analysis — Target: {result.target or target}

## Scan Quality: {quality_str}
{quality_reason}

## Scan Metrics
{metrics_block}

## VERIFIED OPEN PORTS
{ports_block}

## Ignored / Discarded Data
{ignored_block}
{ctx_block}

## Your Analysis Task

The scan returned ZERO confirmed open ports.

Your response MUST follow this exact structure:

### Scan Quality
**{quality_str}**
Reason: {quality_reason}

### Verified Open Ports
None confirmed.

### Ignored Data
{chr(10).join(f'- {ig}' for ig in result.ignored_data[:5]) if result.ignored_data else '- None'}

### Attack Surface
Insufficient reliable data. Further enumeration required.

### Next Steps (Actionable)
Provide ONLY commands to improve scan coverage — do NOT suggest attacking services that were not confirmed.
Suggest commands from this list as appropriate:
- `nmap -sV -sC -p- {result.target or target}` — add version/script detection
- `nmap -Pn -sS {result.target or target}` — skip host discovery (if host appears down)
- `nmap -sU --top-ports 100 {result.target or target}` — check top UDP ports
- `nmap -T2 --max-retries 3 {result.target or target}` — slow down for filtered hosts
"""

        # Full analysis template for scans with confirmed open ports
        return f"""{ANTI_HALLUCINATION_RULES}

# Nmap Analysis — Target: {result.target or target}

## Scan Quality: {quality_str}
{quality_reason}

## Scan Metrics
{metrics_block}

## VERIFIED OPEN PORTS (source of truth — analyse ONLY these)
{ports_block}

## Ignored / Discarded Data
{ignored_block}
{ctx_block}

## Your Analysis Task

Analyse ONLY the verified open ports listed above. Do not mention any other services.

Your response MUST follow this exact structure:

### Scan Quality
State: **{quality_str}**
Reason: {quality_reason}
Impact on analysis: [explain how quality affects confidence of findings]

### Verified Open Ports
For each port in the verified list above:
| Port | Protocol | Service | Version | Confidence | Attacker Significance |
|------|----------|---------|---------|------------|----------------------|
[fill in one row per verified port only — copy port numbers exactly as listed above]

### Ignored Data
List what was discarded and why. Do not re-introduce ignored data as findings.

### Attack Surface (Realistic — prioritised)
Rank attack vectors based ONLY on verified ports. Use this priority:
1. Web services (HTTP/HTTPS) → look for exploitable apps/misconfigs
2. Known vulnerable service versions → check CVEs
3. Misconfigured services → auth bypass, anonymous access
4. Credential-based attacks → only as last resort, not first

For each attack vector:
- Target: [exact port/service from verified list]
- Technique: [specific technique]
- Rationale: [why this is viable given the confirmed data]
- Difficulty: Easy / Medium / Hard

### Next Steps (Actionable Commands)
Provide exact commands. Every command must target a SPECIFIC port from the verified list.
No generic advice — real commands a pentester would run right now.
"""

    @staticmethod
    def _nmap_from_raw(
        scan_output: str,
        target: str,
        extra_context: Optional[str] = None,
    ) -> str:
        """
        Legacy fallback: filter raw text minimally before sending.
        Pre-filter noise lines before building the prompt.
        """
        from parsers.nmap_parser import _NOISE_PATTERNS

        # Strip noise
        clean_lines = []
        for line in scan_output.splitlines():
            if not any(p.search(line.strip()) for p in _NOISE_PATTERNS):
                clean_lines.append(line)
        clean = "\n".join(clean_lines)

        # Count open ports
        open_count = len(re.findall(r"^\d+/(?:tcp|udp)\s+open", clean, re.M))

        ctx_block = f"\n## Previous Context\n{extra_context}\n" if extra_context else ""

        return f"""{ANTI_HALLUCINATION_RULES}

# Nmap Analysis — Target: {target}

## Pre-filtered Scan Output (noise stripped)
Confirmed open port lines found: {open_count}

```
{clean[:8000]}
```
{ctx_block}

## Your Analysis Task

{ANTI_HALLUCINATION_RULES}

Analyse ONLY ports with "open" state from the scan above.
Follow the exact output structure:

### Scan Quality
[High/Medium/Low + reason]

### Verified Open Ports
[Only ports with state=open from the scan above]

### Ignored Data
[What you are NOT analysing and why]

### Attack Surface (Realistic)
[Only for confirmed open ports]

### Next Steps (Actionable Commands)
[Exact commands referencing specific confirmed ports]
"""

    # ── gobuster / ffuf / dirsearch ──────────────────────────────────────────

    @staticmethod
    def web_directory_analysis(
        scan_output: str,
        target: str,
        base_url: Optional[str] = None,
        extra_context: Optional[str] = None,
    ) -> str:
        url_line  = f"\nBase URL: {base_url}" if base_url else ""
        ctx_block = f"\n## Previous Context\n{extra_context}\n" if extra_context else ""

        # Filter out non-result lines
        clean_lines = [
            line for line in scan_output.splitlines()
            if not line.startswith("Error") and "gobuster" not in line.lower()[:20]
        ]
        clean = "\n".join(clean_lines)

        return f"""You are an expert web application penetration tester.
RULE: Only analyse paths that actually appear in the scan output below. Do not invent paths.

# Web Directory Discovery — Target: {target}{url_line}

## Tool Output
```
{clean[:8000]}
```
{ctx_block}

## Your Task (follow this structure exactly)

### Scan Quality
[Were results found? How complete does this appear?]

### Interesting Paths Found
For each discovered path that could be useful to an attacker:
| Path | Status Code | Notes |
|------|-------------|-------|
[Only paths from the output above]

### Attack Vectors
For each interesting path from the scan:
- **Path**: [exact path from output]
- **Technique**: [specific attack technique]
- **Why**: [reason this is interesting]

### Recommended Follow-Up Commands
Exact commands to dig deeper into discovered paths.

### Priority
Top 3 paths to attack first, with rationale.
"""

    # ── linpeas / linenum ────────────────────────────────────────────────────

    @staticmethod
    def privesc_analysis(
        scan_output: str,
        target: str,
        current_user: Optional[str] = None,
        extra_context: Optional[str] = None,
    ) -> str:
        user_line = f"\nCurrent user: {current_user}" if current_user else ""
        ctx_block = f"\n## Previous Notes\n{extra_context}\n" if extra_context else ""

        # Cap output size
        if len(scan_output) > 9000:
            scan_output = (
                scan_output[:4500]
                + "\n\n[... TRUNCATED ...]\n\n"
                + scan_output[-4000:]
            )

        return f"""You are an expert Linux privilege escalation specialist.
RULE: Only reference specific paths, binaries, and users that appear in the output below.
Do not suggest generic privesc techniques that aren't supported by the actual findings.

# Privilege Escalation Analysis — Target: {target}{user_line}

## LinPEAS Output
```
{scan_output}
```
{ctx_block}

## Your Task (follow this structure exactly)

### Scan Quality
[How complete/useful is this output?]

### Critical Findings (High Confidence)
Only findings directly evidenced in the output above.
For each:
- **Finding**: [exact path/binary/config from output]
- **Type**: [SUID / Sudo / Cron / Password / Kernel / Writable path / etc.]
- **Exploit**: [specific technique]
- **Command**: [exact command to exploit it]

### Credential Findings
Any usernames, passwords, keys visible in the output.

### Persistence Opportunities
Cron jobs, writable startup files, etc. — evidenced in the output only.

### Immediate Action Plan
Top 3 commands to run right now, in order of likelihood to succeed.
"""

    # ── generic ──────────────────────────────────────────────────────────────

    @staticmethod
    def generic_recon_analysis(
        tool_name: str,
        scan_output: str,
        target: str,
        extra_context: Optional[str] = None,
    ) -> str:
        ctx_block = f"\n## Previous Context\n{extra_context}\n" if extra_context else ""
        return f"""You are an expert penetration tester.
RULE: Only discuss findings that appear in the tool output below. Do not invent data.

# {tool_name} Output Analysis — Target: {target}

## Tool Output
```
{scan_output[:8000]}
```
{ctx_block}

## Your Task

### Key Findings
What does this output actually show? Reference specific lines.

### Security Implications
Based only on confirmed data above, what does this mean for the target's security?

### Attack Paths
Specific vectors based on confirmed findings only.

### Next Steps
Exact commands to follow up on these findings.
"""

    # ── combined summary ─────────────────────────────────────────────────────

    @staticmethod
    def combined_recon_summary(
        findings: dict[str, str],
        target: str,
    ) -> str:
        sections = "\n\n".join(
            f"### {tool}\n```\n{output[:2000]}\n```"
            for tool, output in findings.items()
        )
        return f"""You are an expert penetration tester synthesising multiple recon scans.
RULE: Only reference services and ports that appear in the tool outputs below.

# Combined Reconnaissance — Target: {target}

{sections}

## Your Task

### Confirmed Attack Surface
Services and ports that appear across multiple scans (highest confidence).

### Master Attack Plan
Step-by-step from initial access to full compromise — based ONLY on confirmed data above.
Number each step. Include exact tool commands.

### Risk Rating
Easy / Medium / Hard — justify based on confirmed findings.
"""


# ── need import at module level for _nmap_from_raw ───────────────────────────
import re
