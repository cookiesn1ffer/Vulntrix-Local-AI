"""
GenericParser — pass-through parser for unrecognised tool output.

Simply reads the file, applies light sanitisation, and returns the
raw text so it can be fed directly to the AI for analysis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")


@dataclass
class GenericResult:
    tool_hint: str = "unknown"
    raw_text : str = ""
    file_path: str = ""

    def summary(self) -> str:
        preview = self.raw_text[:500]
        return f"Tool: {self.tool_hint}\nFile: {self.file_path}\n\nPreview:\n{preview}"


class GenericParser:
    """Minimal parser — just load and clean the file."""

    @classmethod
    def from_file(cls, path: str | Path, tool_hint: str = "") -> GenericResult:
        p    = Path(path)
        text = p.read_text(errors="replace")
        return cls.from_string(text, tool_hint=tool_hint or p.stem, file_path=str(p))

    @classmethod
    def from_string(
        cls,
        text: str,
        tool_hint: str = "unknown",
        file_path: str = "",
    ) -> GenericResult:
        clean = _ANSI_RE.sub("", text)
        # Collapse excessive blank lines
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return GenericResult(tool_hint=tool_hint, raw_text=clean.strip(), file_path=file_path)
