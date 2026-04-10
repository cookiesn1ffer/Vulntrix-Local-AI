"""
CLICommands — the business logic behind each CLI command.

This layer is intentionally separated from the UI (interface.py) so the
same logic can be invoked programmatically or unit-tested without a
terminal.

Each public method returns a string (the result to display) and may also
update the target's TargetContext as a side effect.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Generator, Optional

from ai_core   import ModelRouter
from context   import SessionStore, TargetContext
from parsers   import FileLoader
from parsers.nmap_parser     import NmapResult
from parsers.gobuster_parser import GobusterResult
from parsers.linpeas_parser  import LinpeasResult
from parsers.generic_parser  import GenericResult
from prompts   import ReconPrompts, ExploitPrompts, SystemPrompts


class CLICommands:
    """
    Stateful command handler.  Holds a reference to the active
    TargetContext so commands accumulate state across a session.
    """

    def __init__(
        self,
        router       : ModelRouter,
        store        : SessionStore,
        stream_cb    : Optional[Callable[[str], None]] = None,
    ) -> None:
        self.router    = router
        self.store     = store
        # stream_cb is called with each token when streaming output
        self.stream_cb = stream_cb or (lambda t: (sys.stdout.write(t), sys.stdout.flush()))
        self._ctx: Optional[TargetContext] = None

    # ─── target management ───────────────────────────────────────────────────

    def cmd_target(self, target: str) -> str:
        """Set the active target."""
        self._ctx = TargetContext(target)
        self.store.set_current(target)
        msg = f"Active target set to: {target}"
        if self._ctx.exists():
            msg += "\n" + self._ctx.context_summary()
        return msg

    def cmd_targets(self) -> str:
        """List all saved targets."""
        targets = self.store.list_targets()
        if not targets:
            return "No saved targets found."
        current = self.store.get_current() or ""
        lines = ["Saved targets:"]
        for t in targets:
            marker = " ◀ current" if t == current else ""
            lines.append(f"  {t}{marker}")
        return "\n".join(lines)

    # ─── recon command ───────────────────────────────────────────────────────

    def cmd_recon(
        self,
        file_path: str,
        tool_override: Optional[str] = None,
        stream: bool = True,
    ) -> str:
        """
        Analyse a scan output file and return AI-generated findings.
        Updates target context with key data.
        """
        self._require_target()
        ctx = self._ctx

        # Load and parse file
        try:
            tool_type, result = FileLoader.load(file_path)
        except FileNotFoundError as exc:
            return f"Error: {exc}"

        # Override tool detection if user specified
        if tool_override:
            tool_type = tool_override.lower()

        # Build prompt based on tool type
        extra_context = ctx.get_all_analysis()

        if isinstance(result, NmapResult):
            # Only save metadata when scan has reliable data
            if result.target and result.has_reliable_data:
                ctx.set_metadata(
                    ip         = result.target,
                    hostname   = result.hostname,
                    os_guess   = result.os_guess,
                    open_ports = [p.port for p in result.open_ports],
                    services   = {str(p.port): p.service for p in result.open_ports},
                )
            # Pass validated NmapResult so AI only sees confirmed ports
            prompt = ReconPrompts.nmap_analysis(
                scan_output   = result.clean_text,
                target        = result.target or ctx.target_id,
                extra_context = extra_context or None,
                nmap_result   = result,
            )
        elif isinstance(result, GobusterResult):
            prompt = ReconPrompts.web_directory_analysis(
                scan_output   = result.raw_text,
                target        = ctx.target_id,
                base_url      = result.target_url or None,
                extra_context = extra_context or None,
            )
        elif isinstance(result, LinpeasResult):
            prompt = ReconPrompts.privesc_analysis(
                scan_output   = result.top_sections_text(),
                target        = ctx.target_id,
                current_user  = result.current_user or None,
                extra_context = extra_context or None,
            )
        else:
            # GenericResult
            name = Path(file_path).stem
            prompt = ReconPrompts.generic_recon_analysis(
                tool_name     = name,
                scan_output   = result.raw_text[:6000],
                target        = ctx.target_id,
                extra_context = extra_context or None,
            )

        # Run analysis
        analysis = self._run_streaming(
            prompt, SystemPrompts.REASONING, stream
        )
        # Cache result
        ctx.save_analysis(tool_type, analysis)
        ctx.save()
        return analysis

    # ─── exploit command ─────────────────────────────────────────────────────

    def cmd_exploit(
        self,
        vuln_type: str,
        target: Optional[str] = None,
        lhost: Optional[str] = None,
        lport: int = 4444,
        language: str = "python",
        details: Optional[str] = None,
        stream: bool = True,
    ) -> str:
        """
        Generate exploit code for a specified vulnerability type.
        """
        self._require_target()
        ctx = self._ctx

        t         = target or ctx.target_id
        context   = ctx.context_summary()
        vuln_lower = vuln_type.lower()

        if "reverse" in vuln_lower or "shell" in vuln_lower:
            prompt = ExploitPrompts.reverse_shell(
                target_os = ctx._data.os_guess or "linux",
                lhost     = lhost or "10.10.14.1",
                lport     = lport,
                language  = language,
                context   = context,
            )
        elif "webshell" in vuln_lower or "web shell" in vuln_lower:
            prompt = ExploitPrompts.web_shell(
                language = language,
                context  = context,
            )
        elif "sqli" in vuln_lower or "sql" in vuln_lower:
            prompt = ExploitPrompts.sqli_exploit(
                target_url = t,
                parameter  = "id",
                context    = context,
            )
        elif "lfi" in vuln_lower or "traversal" in vuln_lower:
            prompt = ExploitPrompts.lfi_exploit(
                target_url = t,
                parameter  = "file",
                server_os  = ctx._data.os_guess or "linux",
                context    = context,
            )
        elif "privesc" in vuln_lower or "priv esc" in vuln_lower:
            prompt = ExploitPrompts.privesc_script(
                vector    = details or "suid",
                target_os = ctx._data.os_guess or "linux",
                context   = context,
            )
        else:
            prompt = ExploitPrompts.custom_exploit(
                vulnerability = vuln_type,
                target        = t,
                language      = language,
                details       = details,
                context       = context,
            )

        code = self._run_coding_stream(prompt, stream)
        ctx.add_note(f"exploit_{vuln_lower[:20]}", code[:300] + "…")
        ctx.save()
        return code

    # ─── notes commands ──────────────────────────────────────────────────────

    def cmd_note_add(self, label: str, content: str) -> str:
        self._require_target()
        self._ctx.add_note(label, content)
        self._ctx.save()
        return f"Note '{label}' saved."

    def cmd_note_list(self) -> str:
        self._require_target()
        notes = self._ctx.list_notes()
        if not notes:
            return "No notes for this target."
        lines = [f"Notes for {self._ctx.target_id}:"]
        for label, content in notes.items():
            lines.append(f"\n  [{label}]\n  {content[:200]}")
        return "\n".join(lines)

    def cmd_note_get(self, label: str) -> str:
        self._require_target()
        note = self._ctx.get_note(label)
        return note if note else f"No note with label '{label}'."

    def cmd_note_delete(self, label: str) -> str:
        self._require_target()
        if self._ctx.delete_note(label):
            self._ctx.save()
            return f"Note '{label}' deleted."
        return f"Note '{label}' not found."

    # ─── credentials commands ─────────────────────────────────────────────────

    def cmd_cred_add(
        self, username: str, password: str = "",
        service: str = "", source: str = "",
    ) -> str:
        self._require_target()
        self._ctx.add_credential(username, password, service=service, source=source)
        self._ctx.save()
        return f"Credential saved: {username}:{password} ({service})"

    def cmd_cred_list(self) -> str:
        self._require_target()
        creds = self._ctx.list_credentials()
        if not creds:
            return "No credentials saved for this target."
        lines = [f"Credentials for {self._ctx.target_id}:"]
        for c in creds:
            lines.append(f"  {c['username']}:{c['password'] or c['hash_val']} "
                         f"[{c['service']}] (from {c['source']})")
        return "\n".join(lines)

    # ─── attack chain ─────────────────────────────────────────────────────────

    def cmd_chain_add(self, stage_name: str) -> str:
        self._require_target()
        self._ctx.add_attack_stage(stage_name)
        self._ctx.save()
        return f"Attack stage added: {stage_name}"

    def cmd_chain_update(self, stage_name: str, status: str, notes: str = "") -> str:
        self._require_target()
        if self._ctx.update_attack_stage(stage_name, status, notes):
            self._ctx.save()
            return f"Stage '{stage_name}' → {status}"
        return f"Stage '{stage_name}' not found."

    def cmd_chain_show(self) -> str:
        self._require_target()
        chain = self._ctx.get_attack_chain()
        if not chain:
            return "Attack chain is empty."
        STATUS_ICONS = {"pending": "⏳", "in_progress": "🔄", "done": "✅", "failed": "❌"}
        lines = [f"Attack chain for {self._ctx.target_id}:"]
        for i, s in enumerate(chain, 1):
            icon = STATUS_ICONS.get(s["status"], "?")
            notes_part = f" — {s['notes'][:60]}" if s.get("notes") else ""
            lines.append(f"  {i}. {icon} {s['name']}{notes_part}")
        return "\n".join(lines)

    # ─── summary / status ────────────────────────────────────────────────────

    def cmd_status(self) -> str:
        self._require_target()
        return self._ctx.context_summary()

    def cmd_models(self) -> str:
        """Check which models are available in Ollama."""
        try:
            status = self.router.check_models()
            lines  = ["Model availability:"]
            for model, available in status.items():
                icon = "✅" if available else "❌"
                lines.append(f"  {icon}  {model}")
            try:
                all_models = self.router.client.list_models()
                lines.append("\nAll available models:")
                for m in sorted(all_models):
                    lines.append(f"  • {m}")
            except Exception:
                pass
            return "\n".join(lines)
        except Exception as exc:
            return f"Could not reach Ollama: {exc}"

    # ─── combined recon summary ──────────────────────────────────────────────

    def cmd_summarise(self, stream: bool = True) -> str:
        """Generate a master attack summary from all cached analyses."""
        self._require_target()
        ctx = self._ctx

        analyses = ctx._data.analysis
        if not analyses:
            return "No analyses cached yet.  Run 'recon' on some scan files first."

        prompt = ReconPrompts.combined_recon_summary(
            findings = {k: v for k, v in analyses.items()},
            target   = ctx.target_id,
        )
        summary = self._run_streaming(prompt, SystemPrompts.REASONING, stream)
        ctx.add_note("master_summary", summary[:1000])
        ctx.save()
        return summary

    # ─── payload encoding ────────────────────────────────────────────────────

    def cmd_encode(self, payload: str, technique: str = "base64", stream: bool = True) -> str:
        """Encode / obfuscate a payload."""
        self._require_target()
        prompt = ExploitPrompts.encode_payload(
            raw_payload    = payload,
            technique      = technique,
            target_context = self._ctx.context_summary(),
        )
        return self._run_coding_stream(prompt, stream)

    # ─── private helpers ─────────────────────────────────────────────────────

    def _require_target(self) -> None:
        if self._ctx is None:
            # Try to restore last session
            current = self.store.get_current()
            if current:
                self._ctx = TargetContext(current)
            else:
                raise RuntimeError(
                    "No active target.  Run: target <IP or hostname>"
                )

    def _run_streaming(
        self,
        prompt: str,
        system: str,
        stream: bool,
    ) -> str:
        if stream and self.stream_cb:
            parts: list[str] = []
            for token in self.router.stream_analyse(prompt, system=system):
                self.stream_cb(token)
                parts.append(token)
            sys.stdout.write("\n")
            return "".join(parts)
        return self.router.analyse(prompt, system=system, stream=False)

    def _run_coding_stream(self, prompt: str, stream: bool) -> str:
        if stream and self.stream_cb:
            parts: list[str] = []
            for token in self.router.stream_code(prompt, system=SystemPrompts.CODING):
                self.stream_cb(token)
                parts.append(token)
            sys.stdout.write("\n")
            return "".join(parts)
        return self.router.code(prompt, system=SystemPrompts.CODING, stream=False)
