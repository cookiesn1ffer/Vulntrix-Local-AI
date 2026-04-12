"""
SessionStore — list, load, and manage all saved target contexts.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .target_context import TargetContext


class SessionStore:
    """
    Enumerate and access all persisted target contexts.

    Usage
    -----
    store = SessionStore()
    for name in store.list_targets():
        ctx = store.load(name)
        print(ctx.context_summary())
    """

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else TargetContext.DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # guards set_current / get_current

    def list_targets(self) -> list[str]:
        """Return sorted list of saved target IDs."""
        return sorted(
            p.stem for p in self.data_dir.glob("*.json")
        )

    def load(self, target_id: str) -> TargetContext:
        return TargetContext(target_id, data_dir=self.data_dir)

    def delete(self, target_id: str) -> bool:
        ctx = TargetContext(target_id, data_dir=self.data_dir)
        return ctx.delete()

    def search(self, keyword: str) -> list[str]:
        """Return target IDs whose notes or metadata contain *keyword*."""
        results = []
        for tid in self.list_targets():
            ctx = self.load(tid)
            summary = ctx.context_summary(max_chars=5000).lower()
            if keyword.lower() in summary:
                results.append(tid)
        return results

    def current_target_path(self) -> Path:
        """Path to the 'current target' pointer file."""
        return self.data_dir.parent / "current_target"

    def set_current(self, target_id: str) -> None:
        with self._lock:
            self.current_target_path().write_text(target_id, encoding="utf-8")

    def get_current(self) -> Optional[str]:
        with self._lock:
            p = self.current_target_path()
            if p.exists():
                tid = p.read_text(encoding="utf-8").strip()
                return tid if tid else None
            return None

    def clear_current(self) -> None:
        with self._lock:
            p = self.current_target_path()
            try:
                p.unlink(missing_ok=True)
            except OSError:
                # Fallback to empty pointer if unlink is blocked.
                p.write_text("", encoding="utf-8")

    def wipe_all(self) -> dict[str, int]:
        """
        Delete all persisted target data.

        Returns counts of removed data files and lock files.
        """
        json_deleted = 0
        lock_deleted = 0
        for p in self.data_dir.glob("*.json"):
            try:
                p.unlink()
                json_deleted += 1
            except OSError:
                pass
        for p in self.data_dir.glob("*.lock"):
            try:
                p.unlink()
                lock_deleted += 1
            except OSError:
                pass
        self.clear_current()
        return {"targets_deleted": json_deleted, "locks_deleted": lock_deleted}
