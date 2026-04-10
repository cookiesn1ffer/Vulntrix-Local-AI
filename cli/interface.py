"""
PentestCLI — interactive REPL and argument-based entry point.

Uses the 'rich' library for coloured output and 'prompt_toolkit' for
the interactive shell.  Falls back to plain readline if either library
is unavailable.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Optional

# ─── optional rich imports (graceful fallback) ───────────────────────────────
try:
    from rich.console  import Console
    from rich.markdown import Markdown
    from rich.panel    import Panel
    from rich.text     import Text
    from rich.syntax   import Syntax
    _RICH = True
    console = Console()
except ImportError:
    _RICH = False
    console = None  # type: ignore

# ─── optional prompt_toolkit ─────────────────────────────────────────────────
try:
    from prompt_toolkit                     import PromptSession
    from prompt_toolkit.history             import FileHistory
    from prompt_toolkit.auto_suggest        import AutoSuggestFromHistory
    from prompt_toolkit.completion          import WordCompleter
    _PT = True
except ImportError:
    _PT = False

from ai_core  import ModelRouter
from context  import SessionStore
from .commands import CLICommands


# ─── helpers ─────────────────────────────────────────────────────────────────

BANNER = r"""
  ____            _            _       _    ___
 |  _ \ ___ _ __ | |_ ___  ___| |_    / \  |_ _|
 | |_) / _ \ '_ \| __/ _ \/ __| __|  / _ \  | |
 |  __/  __/ | | | ||  __/\__ \ |_  / ___ \ | |
 |_|   \___|_| |_|\__\___||___/\__|/_/   \_\___|

  Local AI-Powered Penetration Testing Assistant
  Powered by Ollama  |  Lab / CTF Use Only
"""

HELP_TEXT = """
Commands
────────
  target  <ip/hostname>     Set active target (creates context if new)
  targets                   List all saved targets

  recon   <file>            Analyse scan output (nmap/gobuster/linpeas/any)
  exploit <type> [options]  Generate exploit code
    types: reverse-shell, webshell, sqli, lfi, privesc, custom:<desc>
    options:
      --lhost <ip>          Attacker IP for callbacks (default: 10.10.14.1)
      --lport <port>        Listener port (default: 4444)
      --lang  <lang>        Code language: python|bash|powershell (default: python)
      --detail <text>       Extra context for the AI

  summarise                 Generate master attack plan from all recon
  encode  <payload> [--technique base64|hex|url]   Encode a payload

  note add    <label> <text>    Add a note to current target
  note list                     List all notes
  note get    <label>           Print a specific note
  note del    <label>           Delete a note

  cred add    <user> <pass> [--service <svc>]    Save credential
  cred list                     List saved credentials

  chain add   <stage>           Add attack chain stage
  chain done  <stage> [notes]   Mark stage complete
  chain fail  <stage> [reason]  Mark stage failed
  chain show                    Show attack chain progress

  status                    Show full context summary for active target
  models                    Check Ollama model availability
  clear                     Clear screen
  help                      Show this message
  exit / quit               Exit

Examples
────────
  target 10.10.10.1
  recon /tmp/nmap.txt
  recon /tmp/gobuster.txt
  exploit reverse-shell --lhost 10.10.14.5 --lport 9001
  exploit sqli --detail "GET /search?q= returns different errors on quotes"
  note add foothold "SSH as www-data via LFI log poisoning"
  chain add "Initial Access"
  chain done "Initial Access" "Got www-data shell via log poisoning"
"""

COMMANDS = [
    "target", "targets", "recon", "exploit", "summarise", "encode",
    "note", "cred", "chain", "status", "models", "clear", "help", "exit", "quit",
]


def _print(text: str, style: str = "") -> None:
    if _RICH:
        console.print(text, style=style or None)
    else:
        print(text)


def _print_md(text: str) -> None:
    if _RICH:
        console.print(Markdown(text))
    else:
        print(text)


def _print_panel(text: str, title: str = "", style: str = "cyan") -> None:
    if _RICH:
        console.print(Panel(text, title=title, border_style=style))
    else:
        if title:
            print(f"\n{'─' * 60}\n  {title}\n{'─' * 60}")
        print(text)


def _print_code(code: str, language: str = "python") -> None:
    if _RICH:
        syntax = Syntax(code, language, theme="monokai", line_numbers=True)
        console.print(syntax)
    else:
        print(code)


def _stream_token(token: str) -> None:
    sys.stdout.write(token)
    sys.stdout.flush()


# ─── main CLI class ──────────────────────────────────────────────────────────

class PentestCLI:
    """
    Interactive CLI REPL for the pentest AI assistant.
    """

    def __init__(self, router: ModelRouter) -> None:
        self.router   = router
        self.store    = SessionStore()
        self.commands = CLICommands(
            router    = router,
            store     = self.store,
            stream_cb = _stream_token,
        )

    # ─── entry points ────────────────────────────────────────────────────────

    def run_interactive(self) -> None:
        """Start the interactive REPL."""
        if _RICH:
            console.print(BANNER, style="bold green")
        else:
            print(BANNER)

        # Restore last active target if any
        current = self.store.get_current()
        if current:
            _print(f"  Resuming session for: [bold cyan]{current}[/]" if _RICH
                   else f"  Resuming session for: {current}", style="")

        _print("\n  Type 'help' for commands, 'exit' to quit.\n")

        session = self._make_prompt_session()

        while True:
            try:
                if _PT and session:
                    raw = session.prompt("vulntrix » ")
                else:
                    raw = input("vulntrix » ")
            except (KeyboardInterrupt, EOFError):
                _print("\nBye!", style="bold yellow")
                break

            line = raw.strip()
            if not line:
                continue

            if line.lower() in ("exit", "quit"):
                _print("Bye!", style="bold yellow")
                break

            self._dispatch(line)

    def run_command(self, line: str) -> str:
        """Execute a single command line (non-interactive mode)."""
        return self._dispatch_return(line)

    # ─── dispatcher ──────────────────────────────────────────────────────────

    def _dispatch(self, line: str) -> None:
        result = self._dispatch_return(line)
        if result:
            _print_md(result)

    def _dispatch_return(self, line: str) -> str:
        tokens = line.split()
        if not tokens:
            return ""
        cmd    = tokens[0].lower()
        args   = tokens[1:]

        try:
            # ── single-word commands ──────────────────────────────────────
            if cmd == "help":
                _print_panel(HELP_TEXT, title="Help", style="green")
                return ""

            if cmd == "clear":
                if _RICH:
                    console.clear()
                else:
                    import os; os.system("clear" if sys.platform != "win32" else "cls")
                return ""

            if cmd == "models":
                return self.commands.cmd_models()

            if cmd == "targets":
                return self.commands.cmd_targets()

            if cmd == "status":
                return self.commands.cmd_status()

            if cmd == "summarise":
                return self.commands.cmd_summarise()

            # ── target ───────────────────────────────────────────────────
            if cmd == "target":
                if not args:
                    return "Usage: target <ip/hostname>"
                return self.commands.cmd_target(args[0])

            # ── recon ────────────────────────────────────────────────────
            if cmd == "recon":
                if not args:
                    return "Usage: recon <file> [--tool nmap|gobuster|linpeas|generic]"
                file_path     = args[0]
                tool_override = self._flag(args, "--tool")
                return self.commands.cmd_recon(file_path, tool_override=tool_override)

            # ── exploit ──────────────────────────────────────────────────
            if cmd == "exploit":
                if not args:
                    return "Usage: exploit <type> [--lhost IP] [--lport N] [--lang py|bash] [--detail TEXT]"
                vuln_type = args[0]
                lhost  = self._flag(args, "--lhost")
                lport  = int(self._flag(args, "--lport") or 4444)
                lang   = self._flag(args, "--lang") or self._flag(args, "--language") or "python"
                detail = self._flag(args, "--detail")
                return self.commands.cmd_exploit(
                    vuln_type = vuln_type,
                    lhost     = lhost,
                    lport     = lport,
                    language  = lang,
                    details   = detail,
                )

            # ── encode ───────────────────────────────────────────────────
            if cmd == "encode":
                if not args:
                    return "Usage: encode <payload> [--technique base64|hex|url]"
                technique = self._flag(args, "--technique") or "base64"
                payload   = self._consume_positional(args, "--technique")
                return self.commands.cmd_encode(payload, technique)

            # ── note ─────────────────────────────────────────────────────
            if cmd == "note":
                sub = args[0].lower() if args else ""
                if sub == "add" and len(args) >= 3:
                    return self.commands.cmd_note_add(args[1], " ".join(args[2:]))
                if sub == "list":
                    return self.commands.cmd_note_list()
                if sub == "get" and len(args) >= 2:
                    return self.commands.cmd_note_get(args[1])
                if sub in ("del", "delete") and len(args) >= 2:
                    return self.commands.cmd_note_delete(args[1])
                return "Usage: note add|list|get|del <args>"

            # ── cred ─────────────────────────────────────────────────────
            if cmd == "cred":
                sub = args[0].lower() if args else ""
                if sub == "add" and len(args) >= 3:
                    svc = self._flag(args[3:], "--service") or ""
                    return self.commands.cmd_cred_add(args[1], args[2], service=svc)
                if sub == "list":
                    return self.commands.cmd_cred_list()
                return "Usage: cred add <user> <pass> [--service <svc>] | cred list"

            # ── chain ────────────────────────────────────────────────────
            if cmd == "chain":
                sub = args[0].lower() if args else ""
                if sub == "add" and len(args) >= 2:
                    return self.commands.cmd_chain_add(" ".join(args[1:]))
                if sub == "done" and len(args) >= 2:
                    stage = args[1]
                    notes = " ".join(args[2:])
                    return self.commands.cmd_chain_update(stage, "done", notes)
                if sub == "fail" and len(args) >= 2:
                    stage  = args[1]
                    reason = " ".join(args[2:])
                    return self.commands.cmd_chain_update(stage, "failed", reason)
                if sub == "show":
                    return self.commands.cmd_chain_show()
                return "Usage: chain add|done|fail|show <args>"

            return f"Unknown command: '{cmd}'.  Type 'help' for usage."

        except RuntimeError as exc:
            return f"[red]Error:[/] {exc}" if _RICH else f"Error: {exc}"
        except Exception as exc:
            return (f"[bold red]Unexpected error:[/] {exc}"
                    if _RICH else f"Unexpected error: {exc}")

    # ─── argument helpers ────────────────────────────────────────────────────

    @staticmethod
    def _flag(args: list[str], flag: str) -> Optional[str]:
        """Return the value after *flag*, or None if not present."""
        try:
            i = args.index(flag)
            return args[i + 1]
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _consume_positional(args: list[str], *exclude_flags: str) -> str:
        """
        Return all positional tokens (those not preceded by a flag)
        joined as a string.  Flag values are excluded.
        """
        skip_next = False
        parts: list[str] = []
        for tok in args:
            if skip_next:
                skip_next = False
                continue
            if tok in exclude_flags:
                skip_next = True
                continue
            if tok.startswith("--"):
                continue
            parts.append(tok)
        return " ".join(parts)

    @staticmethod
    def _make_prompt_session():
        if not _PT:
            return None
        hist_path = Path.home() / ".vulntrix" / "history"
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        completer = WordCompleter(COMMANDS, ignore_case=True)
        return PromptSession(
            history      = FileHistory(str(hist_path)),
            auto_suggest = AutoSuggestFromHistory(),
            completer    = completer,
        )
