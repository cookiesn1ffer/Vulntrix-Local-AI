"""
TargetContext — per-target memory that persists between sessions.

Each target gets a JSON file:
  ~/.vulntrix/targets/<sanitised_target_name>.json

The file stores:
  - Basic target metadata (IP, hostname, OS)
  - Chronological activity log (timestamped entries)
  - Named notes (free text keyed by label)
  - Found credentials
  - Discovered flags (CTF mode)
  - AI-generated analysis summaries (keyed by scan type)
  - Attack chain progress (list of completed/pending stages)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional


# ─── cross-platform file lock ────────────────────────────────────────────────

@contextmanager
def _file_lock(path: Path) -> Generator[None, None, None]:
    """
    Advisory file lock that prevents concurrent writes to the same JSON file.

    - On POSIX (Linux / macOS): uses fcntl.flock — kernel-enforced.
    - On Windows: uses a sibling .lock file with os.open O_CREAT|O_EXCL,
      retrying up to 2 seconds before giving up (best-effort).
    """
    if sys.platform == "win32":
        lock_path = path.with_suffix(".lock")
        deadline  = time.monotonic() + 2.0
        fd        = -1
        while time.monotonic() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        try:
            yield
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        import fcntl
        lock_path = path.with_suffix(".lock")
        lock_path.touch(exist_ok=True)
        with open(lock_path, "r") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


# ─── data models ─────────────────────────────────────────────────────────────

@dataclass
class Credential:
    username : str
    password : str = ""
    hash_val : str = ""
    service  : str = ""
    source   : str = ""
    cracked  : bool = False


@dataclass
class AttackStage:
    name      : str
    status    : str = "pending"   # pending / in_progress / done / failed
    notes     : str = ""
    timestamp : str = ""

    def mark_done(self, notes: str = "") -> None:
        self.status    = "done"
        self.notes     = notes
        self.timestamp = datetime.utcnow().isoformat()

    def mark_failed(self, reason: str = "") -> None:
        self.status    = "failed"
        self.notes     = reason
        self.timestamp = datetime.utcnow().isoformat()


@dataclass
class LogEntry:
    timestamp: str
    category : str   # recon / exploit / note / flag / cred
    content  : str


@dataclass
class TargetData:
    target_id   : str
    ip          : str                  = ""
    hostname    : str                  = ""
    os_guess    : str                  = ""
    open_ports  : list[int]            = field(default_factory=list)
    services    : dict[str, str]       = field(default_factory=dict)  # port → service
    notes       : dict[str, str]       = field(default_factory=dict)
    credentials : list[dict]           = field(default_factory=list)
    flags       : dict[str, str]       = field(default_factory=dict)
    analysis    : dict[str, str]       = field(default_factory=dict)
    attack_chain: list[dict]           = field(default_factory=list)
    log         : list[dict]           = field(default_factory=list)
    created_at  : str                  = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at  : str                  = field(default_factory=lambda: datetime.utcnow().isoformat())


# ─── main class ──────────────────────────────────────────────────────────────

class TargetContext:
    """
    Load, mutate, and persist context for a single pentest target.

    Usage
    -----
    ctx = TargetContext("10.10.10.1")
    ctx.add_note("initial", "Port 22 open with weak key exchange")
    ctx.save()
    """

    DATA_DIR = Path.home() / ".vulntrix" / "targets"

    def __init__(self, target: str, data_dir: Optional[Path] = None) -> None:
        self.target_id = self._sanitise(target)
        self.data_dir  = Path(data_dir) if data_dir else self.DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.data_dir / f"{self.target_id}.json"
        self._data = self._load()

    # ─── metadata ────────────────────────────────────────────────────────────

    def set_metadata(
        self,
        ip       : Optional[str] = None,
        hostname : Optional[str] = None,
        os_guess : Optional[str] = None,
        open_ports: Optional[list[int]] = None,
        services  : Optional[dict[str, str]] = None,
    ) -> None:
        if ip:         self._data.ip         = ip
        if hostname:   self._data.hostname   = hostname
        if os_guess:   self._data.os_guess   = os_guess
        if open_ports: self._data.open_ports = open_ports
        if services:   self._data.services.update(services)
        self._touch()

    # ─── notes ───────────────────────────────────────────────────────────────

    def add_note(self, label: str, content: str) -> None:
        """Add or replace a named note (capped at _MAX_NOTES unique labels)."""
        if label not in self._data.notes and len(self._data.notes) >= self._MAX_NOTES:
            # Evict the oldest entry (insertion-ordered dict in Python 3.7+)
            oldest = next(iter(self._data.notes))
            del self._data.notes[oldest]
        self._data.notes[label] = content
        self._log("note", f"[{label}] {content[:80]}")
        self._touch()

    def get_note(self, label: str) -> Optional[str]:
        return self._data.notes.get(label)

    def list_notes(self) -> dict[str, str]:
        return dict(self._data.notes)

    def delete_note(self, label: str) -> bool:
        if label in self._data.notes:
            del self._data.notes[label]
            self._touch()
            return True
        return False

    # ─── credentials ─────────────────────────────────────────────────────────

    def add_credential(
        self,
        username: str,
        password: str = "",
        hash_val: str = "",
        service : str = "",
        source  : str = "",
    ) -> None:
        if len(self._data.credentials) >= self._MAX_CREDS:
            self._data.credentials.pop(0)   # evict oldest
        cred = Credential(username, password, hash_val, service, source)
        self._data.credentials.append(asdict(cred))
        self._log("cred", f"{username}:{password or hash_val} ({service})")
        self._touch()

    def list_credentials(self) -> list[dict]:
        return list(self._data.credentials)

    # ─── flags ───────────────────────────────────────────────────────────────

    def add_flag(self, name: str, value: str) -> None:
        self._data.flags[name] = value
        self._log("flag", f"{name}: {value}")
        self._touch()

    def list_flags(self) -> dict[str, str]:
        return dict(self._data.flags)

    # ─── analysis cache ──────────────────────────────────────────────────────

    def save_analysis(self, scan_type: str, analysis_text: str) -> None:
        """Cache an AI analysis result for later context injection."""
        self._data.analysis[scan_type] = analysis_text
        self._log("recon", f"Analysis saved for {scan_type}")
        self._touch()

    def get_analysis(self, scan_type: str) -> Optional[str]:
        return self._data.analysis.get(scan_type)

    def get_all_analysis(self) -> str:
        """Return all cached analysis as a single context string."""
        if not self._data.analysis:
            return ""
        parts = []
        for scan_type, text in self._data.analysis.items():
            parts.append(f"## {scan_type} analysis\n{text[:1000]}")
        return "\n\n".join(parts)

    # ─── attack chain ─────────────────────────────────────────────────────────

    def add_attack_stage(self, name: str) -> AttackStage:
        stage = AttackStage(name=name)
        self._data.attack_chain.append(asdict(stage))
        self._touch()
        return stage

    def update_attack_stage(self, name: str, status: str, notes: str = "") -> bool:
        for stage_dict in self._data.attack_chain:
            if stage_dict["name"] == name:
                stage_dict["status"]    = status
                stage_dict["notes"]     = notes
                stage_dict["timestamp"] = datetime.utcnow().isoformat()
                self._touch()
                return True
        return False

    def get_attack_chain(self) -> list[dict]:
        return list(self._data.attack_chain)

    # ─── log ─────────────────────────────────────────────────────────────────

    def log_event(self, category: str, content: str) -> None:
        """Public interface to append a timestamped event to the target log."""
        self._log(category, content)

    def get_log(self, limit: int = 50) -> list[dict]:
        return list(self._data.log[-limit:])

    # ─── context summary (for prompt injection) ───────────────────────────────

    def context_summary(self, max_chars: int = 2000) -> str:
        """
        Return a compact text summary suitable for injection into prompts
        so the AI maintains awareness of multi-stage attack progress.
        """
        parts: list[str] = []

        # Basic info
        info_parts = []
        if self._data.ip:       info_parts.append(f"IP: {self._data.ip}")
        if self._data.hostname: info_parts.append(f"Hostname: {self._data.hostname}")
        if self._data.os_guess: info_parts.append(f"OS: {self._data.os_guess}")
        if self._data.open_ports:
            info_parts.append(f"Open ports: {', '.join(str(p) for p in self._data.open_ports[:10])}")
        if info_parts:
            parts.append("### Target info\n" + " | ".join(info_parts))

        # Notes
        if self._data.notes:
            notes_text = "\n".join(f"- [{k}] {v[:100]}" for k, v in list(self._data.notes.items())[-5:])
            parts.append(f"### Notes\n{notes_text}")

        # Creds
        if self._data.credentials:
            creds = [f"{c['username']}:{c['password'] or c['hash_val']} ({c['service']})"
                     for c in self._data.credentials[:5]]
            parts.append("### Credentials found\n" + "\n".join(f"- {c}" for c in creds))

        # Attack chain
        if self._data.attack_chain:
            chain_text = "\n".join(
                f"- [{s['status'].upper():12}] {s['name']}"
                for s in self._data.attack_chain
            )
            parts.append(f"### Attack chain\n{chain_text}")

        full = "\n\n".join(parts)
        return full[:max_chars]

    # ─── persistence ─────────────────────────────────────────────────────────

    def save(self) -> None:
        with _file_lock(self._path):
            # Atomic write: write to temp file, then rename
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(self._data), indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)  # atomic on POSIX, best-effort on Windows
            # Restrict to owner read/write only — protects stored credentials/notes
            try:
                self._path.chmod(0o600)
            except OSError:
                pass  # Windows ACLs differ; best-effort only

    def delete(self) -> bool:
        if self._path.exists():
            self._path.unlink()
            return True
        return False

    def exists(self) -> bool:
        return self._path.exists()

    # ─── private helpers ─────────────────────────────────────────────────────

    # ─── storage limits ───────────────────────────────────────────────────────

    _MAX_LOG_ENTRIES  = 500   # rotate after this many entries
    _MAX_NOTES        = 200   # hard cap on named notes
    _MAX_CREDS        = 100   # hard cap on stored credentials

    def _load(self) -> TargetData:
        if self._path.exists():
            with _file_lock(self._path):
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    return TargetData(**{k: v for k, v in raw.items()
                                        if k in TargetData.__dataclass_fields__})
                except json.JSONDecodeError as exc:
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        "Corrupt context file %s — resetting (%s)", self._path, exc
                    )
                except Exception:
                    pass
        return TargetData(target_id=self.target_id)

    def _touch(self) -> None:
        self._data.updated_at = datetime.utcnow().isoformat()

    def _log(self, category: str, content: str) -> None:
        entry = LogEntry(
            timestamp = datetime.utcnow().isoformat(),
            category  = category,
            content   = content,
        )
        self._data.log.append(asdict(entry))
        # Rotate: keep only the most recent _MAX_LOG_ENTRIES
        if len(self._data.log) > self._MAX_LOG_ENTRIES:
            self._data.log = self._data.log[-self._MAX_LOG_ENTRIES:]

    @staticmethod
    def _sanitise(target: str) -> str:
        """Make target string safe for use as a filename."""
        return re.sub(r"[^a-zA-Z0-9._\-]", "_", target)[:80]
