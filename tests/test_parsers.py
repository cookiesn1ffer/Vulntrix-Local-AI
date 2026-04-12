"""
test_parsers.py — unit tests for the nmap, gobuster, and linpeas parsers.

Run: pytest tests/ -v
"""

from __future__ import annotations

import pytest

from parsers.nmap_parser import parse_nmap, ScanQuality, NmapParser
from parsers.gobuster_parser import GobusterParser
from parsers.linpeas_parser import LinpeasParser


# ═══════════════════════════════════════════════════════════
# NmapParser
# ═══════════════════════════════════════════════════════════

class TestNmapParser:

    def test_returns_result_object(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        assert result is not None

    def test_open_ports_detected(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        # sample has 22, 80, 139, 445, 3306 open
        open_ports = [p.port for p in result.open_ports]
        assert 22   in open_ports
        assert 80   in open_ports
        assert 3306 in open_ports

    def test_service_names_populated(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        by_port = {p.port: p for p in result.open_ports}
        assert "ssh"  in by_port[22].service.lower()
        assert "http" in by_port[80].service.lower()

    def test_no_filtered_ports_in_open(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        for p in result.open_ports:
            # Anti-hallucination rule: open_ports list should only have open ports
            assert p.state.lower() == "open"

    def test_quality_is_enum(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        assert isinstance(result.scan_quality, ScanQuality)

    def test_empty_input_returns_empty_open_ports(self):
        result = NmapParser.from_string("")
        assert result.open_ports == []

    def test_noise_only_input(self):
        noise = "\n".join([
            "# nmap",
            "Host is up.",
            "Not shown: 999 closed ports",
        ])
        result = NmapParser.from_string(noise)
        assert result.open_ports == []

    def test_filtered_port_not_in_open_ports(self):
        text = "80/tcp  filtered  http\n"
        result = NmapParser.from_string(text)
        open_ports = [p.port for p in result.open_ports]
        assert 80 not in open_ports

    def test_version_string_captured(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        by_port = {p.port: p for p in result.open_ports}
        # OpenSSH version should be in the version field
        assert "openssh" in by_port[22].version.lower()

    def test_parse_from_path(self, tmp_path, sample_nmap):
        f = tmp_path / "scan.txt"
        f.write_text(sample_nmap, encoding="utf-8")
        result = parse_nmap(f)
        assert len(result.open_ports) > 0

    def test_summary_is_string(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        assert isinstance(result.summary(), str)

    def test_to_dict_keys(self, sample_nmap):
        result = NmapParser.from_string(sample_nmap)
        d = result.to_dict()
        for key in ("target", "open_ports", "scan_quality", "has_reliable_data"):
            assert key in d


# ═══════════════════════════════════════════════════════════
# GobusterParser
# ═══════════════════════════════════════════════════════════

class TestGobusterParser:

    def test_returns_result(self, sample_gobuster):
        result = GobusterParser.from_string(sample_gobuster)
        assert result is not None

    def test_paths_discovered(self, sample_gobuster):
        result = GobusterParser.from_string(sample_gobuster)
        assert len(result.paths) > 0

    def test_paths_start_with_slash(self, sample_gobuster):
        result = GobusterParser.from_string(sample_gobuster)
        for p in result.paths:
            assert str(p.path).startswith("/"), f"Path {p.path!r} should start with /"

    def test_status_codes_are_ints(self, sample_gobuster):
        result = GobusterParser.from_string(sample_gobuster)
        for p in result.paths:
            assert isinstance(p.status_code, int)

    def test_interesting_paths_property(self, sample_gobuster):
        result = GobusterParser.from_string(sample_gobuster)
        # interesting_paths is a subset of paths
        interesting = result.interesting_paths
        assert isinstance(interesting, list)
        assert all(p in result.paths for p in interesting)

    def test_empty_input(self):
        result = GobusterParser.from_string("")
        assert result is not None
        assert isinstance(result.paths, list)

    def test_summary_is_string(self, sample_gobuster):
        result = GobusterParser.from_string(sample_gobuster)
        assert isinstance(result.summary(), str)


# ═══════════════════════════════════════════════════════════
# LinpeasParser
# ═══════════════════════════════════════════════════════════

class TestLinpeasParser:

    def test_returns_result(self, sample_linpeas):
        result = LinpeasParser.from_string(sample_linpeas)
        assert result is not None

    def test_has_findings(self, sample_linpeas):
        result = LinpeasParser.from_string(sample_linpeas)
        assert len(result.findings) > 0

    def test_summary_is_string(self, sample_linpeas):
        result = LinpeasParser.from_string(sample_linpeas)
        assert isinstance(result.summary(), str)

    def test_empty_input(self):
        result = LinpeasParser.from_string("")
        assert result is not None
        assert isinstance(result.findings, list)

    def test_critical_findings_is_subset(self, sample_linpeas):
        result = LinpeasParser.from_string(sample_linpeas)
        critical = result.critical_findings
        assert isinstance(critical, list)
