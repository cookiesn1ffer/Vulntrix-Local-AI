"""
Vulntrix — Desktop Launcher  v3.0
A polished GUI dashboard that manages Ollama + the web server,
shows live service status, streams logs, and opens the browser.
Minimises to system tray on close.
"""

from __future__ import annotations

import os
import sys
import time
import signal
import threading
import subprocess
import webbrowser
import platform
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
from typing import Optional, Callable

# ── Constants ──────────────────────────────────────────────────────────────────

ROOT        = Path(__file__).parent
APP_VERSION = "3.0.0"
IS_WINDOWS  = platform.system() == "Windows"

VENV_PY_WIN = ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PY_LIN = ROOT / ".venv" / "bin" / "python"
VENV_PY     = VENV_PY_WIN if IS_WINDOWS else VENV_PY_LIN
APP_PY      = str(VENV_PY) if VENV_PY.exists() else sys.executable

# TLS auto-detection (mirrors web_server.py logic)
_CERT = ROOT / "certs" / "server.crt"
_KEY  = ROOT / "certs" / "server.key"
_TLS  = _CERT.exists() and _KEY.exists()
WEB_PORT   = int(os.environ.get("PORT", "8443" if _TLS else "8000"))
WEB_SCHEME = "https" if _TLS else "http"
WEB_URL    = f"{WEB_SCHEME}://localhost:{WEB_PORT}"

# ── Design tokens ──────────────────────────────────────────────────────────────

C = {
    # Backgrounds
    "bg0":     "#0f0f13",   # deepest — title bar / footer
    "bg1":     "#16161c",   # main window
    "bg2":     "#1e1e26",   # card surfaces
    "bg3":     "#26262e",   # inputs / secondary
    "bg4":     "#2e2e38",   # hover states
    # Borders
    "border":  "#2c2c38",
    "border2": "#38384a",
    # Text
    "text":    "#e4e4f0",
    "text2":   "#9494aa",
    "text3":   "#55556a",
    # Accent (Vulntrix purple)
    "accent":  "#8b6cf7",
    "accent2": "#a78bfa",
    "accent3": "#c4b5fd",
    # Status colours
    "green":   "#4ade80",
    "green2":  "#22c55e",
    "red":     "#f87171",
    "red2":    "#ef4444",
    "yellow":  "#fbbf24",
    "cyan":    "#22d3ee",
    "orange":  "#fb923c",
    "blue":    "#60a5fa",
}

# ── Platform-appropriate font families ────────────────────────────────────────
# tkinter falls back to its default font if the family name is not found,
# so the order matters: list the most specific name per platform.

if IS_WINDOWS:
    _F_UI   = "Segoe UI"
    _F_MONO = "Cascadia Code"
elif sys.platform == "darwin":
    _F_UI   = "SF Pro Display"
    _F_MONO = "Menlo"
else:                                   # Linux / BSD
    _F_UI   = "DejaVu Sans"
    _F_MONO = "DejaVu Sans Mono"

FONT_UI    = (_F_UI,   10)
FONT_BOLD  = (_F_UI,   10, "bold")
FONT_SMALL = (_F_UI,    9)
FONT_MONO  = (_F_MONO,  9)
FONT_TITLE = (_F_UI,   14, "bold")
FONT_HEAD  = (_F_UI,   11, "bold")


# ── Service manager ────────────────────────────────────────────────────────────

class ServiceManager:
    """Manages Ollama + web server subprocess lifecycles."""

    def __init__(self):
        self._ollama:  Optional[subprocess.Popen] = None
        self._server:  Optional[subprocess.Popen] = None
        self._lock     = threading.Lock()
        self.log_cb:    Optional[Callable[[str], None]] = None
        self.status_cb: Optional[Callable[[bool, bool], None]] = None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def ollama_running(self) -> bool:
        return self._ollama is not None and self._ollama.poll() is None

    @property
    def server_running(self) -> bool:
        return self._server is not None and self._server.poll() is None

    # ── Internal helpers ──────────────────────────────────────────────

    def _log(self, text: str):
        if self.log_cb:
            self.log_cb(text)

    def _emit_status(self):
        if self.status_cb:
            self.status_cb(self.ollama_running, self.server_running)

    def _tail(self, proc: subprocess.Popen, label: str):
        try:
            for line in proc.stdout:
                self._log(f"[{label}] {line.decode('utf-8', errors='replace')}")
        except Exception:
            pass

    # ── Ollama ────────────────────────────────────────────────────────

    def start_ollama(self):
        with self._lock:
            if self.ollama_running:
                self._log("[ollama] Already running.\n")
                return
            # Reuse a system-level Ollama if one is already listening
            try:
                import urllib.request
                with urllib.request.urlopen(
                        "http://localhost:11434/api/tags", timeout=2) as r:
                    if r.status == 200:
                        self._log("[ollama] System Ollama detected — reusing it.\n")
                        self._emit_status()
                        return
            except Exception:
                pass
            self._log("[ollama] Starting Ollama...\n")
            kwargs: dict = {}
            if IS_WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                self._ollama = subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
                )
                threading.Thread(
                    target=self._tail, args=(self._ollama, "ollama"), daemon=True
                ).start()
                time.sleep(2)
            except FileNotFoundError:
                self._log("[ollama] ERROR: 'ollama' not found in PATH.\n"
                          "         Download from https://ollama.com\n")
            self._emit_status()

    def stop_ollama(self):
        with self._lock:
            if self._ollama and self._ollama.poll() is None:
                self._log("[ollama] Stopping...\n")
                self._ollama.terminate()
                try:
                    self._ollama.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._ollama.kill()
                self._ollama = None
            self._emit_status()

    # ── Web server ────────────────────────────────────────────────────

    def start_server(self):
        with self._lock:
            if self.server_running:
                self._log("[server] Already running.\n")
                return
            self._log(f"[server] Starting — {WEB_URL}\n")
            flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                     if IS_WINDOWS else 0)
            self._server = subprocess.Popen(
                [APP_PY, "web_server.py"],
                cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            threading.Thread(
                target=self._tail, args=(self._server, "server"), daemon=True
            ).start()
            self._emit_status()

    def stop_server(self):
        with self._lock:
            proc = self._server
            if proc and proc.poll() is None:
                self._log("[server] Stopping...\n")
                if IS_WINDOWS:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    import os as _os
                    try:
                        _os.killpg(_os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                self._server = None
            self._emit_status()

    # ── Combined ──────────────────────────────────────────────────────

    def start_all(self):
        threading.Thread(target=self._start_all_bg, daemon=True).start()

    def _start_all_bg(self):
        self.start_ollama()
        time.sleep(1)
        self.start_server()
        time.sleep(2)
        self._emit_status()

    def stop_all(self):
        self.stop_server()
        self.stop_ollama()

    # ── Model helpers ─────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        try:
            import json, urllib.request
            with urllib.request.urlopen(
                    "http://localhost:11434/api/tags", timeout=3) as r:
                data = json.loads(r.read())
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    def pull_model(self, model: str, progress_cb: Optional[Callable] = None):
        def _run():
            self._log(f"[ollama] Pulling {model}...\n")
            try:
                kwargs: dict = {}
                if IS_WINDOWS:
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                proc = subprocess.Popen(
                    ["ollama", "pull", model],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
                )
                for line in proc.stdout:
                    txt = line.decode("utf-8", errors="replace")
                    self._log(f"[pull] {txt}")
                    if progress_cb:
                        progress_cb(txt.strip())
                proc.wait()
                self._log(f"[ollama] ✓ {model} ready.\n")
                if progress_cb:
                    progress_cb(f"✓ Done: {model}")
            except FileNotFoundError:
                self._log("[ollama] Error: 'ollama' not in PATH.\n")
        threading.Thread(target=_run, daemon=True).start()


# ── Desktop application ────────────────────────────────────────────────────────

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.svc = ServiceManager()
        self.svc.log_cb    = self._append_log
        self.svc.status_cb = self._on_status
        self._tray = None
        self._build_ui()
        self._poll_health()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ══════════════════ UI CONSTRUCTION ═══════════════════════════════

    def _build_ui(self):
        self.title(f"Vulntrix  v{APP_VERSION}")
        self.geometry("1000x660")
        self.minsize(780, 520)
        self.configure(bg=C["bg1"])
        self._apply_ttk_style()

        # ── Title bar ─────────────────────────────────────────────────
        self._build_titlebar()

        # ── Toolbar ───────────────────────────────────────────────────
        self._build_toolbar()

        # ── Content area ──────────────────────────────────────────────
        content = tk.Frame(self, bg=C["bg1"])
        content.pack(fill="both", expand=True, padx=12, pady=8)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # Left: log pane
        self._build_log_pane(content)

        # Right: info / status cards
        self._build_info_pane(content)

        # ── Status bar ────────────────────────────────────────────────
        self._build_statusbar()

    # ── Title bar ──────────────────────────────────────────────────────

    def _build_titlebar(self):
        bar = tk.Frame(self, bg=C["bg0"], height=56)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # Logo circle
        canvas = tk.Canvas(bar, width=34, height=34, bg=C["bg0"],
                           highlightthickness=0)
        canvas.pack(side="left", padx=(18, 8), pady=11)
        canvas.create_oval(2, 2, 32, 32, fill=C["accent"], outline="")
        canvas.create_text(17, 17, text="V", fill="white",
                           font=("Segoe UI" if IS_WINDOWS else "SF Pro Display", 14, "bold"))

        # Name + subtitle
        name_frame = tk.Frame(bar, bg=C["bg0"])
        name_frame.pack(side="left", pady=10)
        tk.Label(name_frame, text="Vulntrix", font=FONT_TITLE,
                 bg=C["bg0"], fg=C["text"]).pack(anchor="w")
        tk.Label(name_frame, text=f"Local AI Pentest Suite  ·  v{APP_VERSION}",
                 font=FONT_SMALL, bg=C["bg0"], fg=C["text3"]).pack(anchor="w")

        # Status indicators (right side)
        status_frame = tk.Frame(bar, bg=C["bg0"])
        status_frame.pack(side="right", padx=20)
        self._dot_ollama = self._make_indicator(status_frame, "Ollama")
        self._dot_server = self._make_indicator(status_frame, "Server")
        self._dot_web    = self._make_indicator(status_frame, "Web UI")

    # ── Toolbar ────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = tk.Frame(self, bg=C["bg2"], height=48)
        tb.pack(fill="x")
        tb.pack_propagate(False)

        inner = tk.Frame(tb, bg=C["bg2"])
        inner.pack(side="left", padx=12, pady=8)

        self._btn_start = self._tb_btn(inner, "▶  Start All",   C["green2"],  self._do_start_all)
        self._btn_stop  = self._tb_btn(inner, "■  Stop All",    C["red2"],    self._do_stop_all)
        self._sep(inner)
        self._btn_open  = self._tb_btn(inner, "⬡  Open UI",    C["accent"],  self._do_open_browser)
        self._sep(inner)
        self._btn_pull  = self._tb_btn(inner, "⬇  Pull Model", C["cyan"],    self._show_pull_dialog)
        self._btn_models= self._tb_btn(inner, "☰  Models",     C["text2"],   self._show_models,
                                       ghost=True)

        # Right: URL chip
        url_frame = tk.Frame(tb, bg=C["bg3"], padx=10, pady=6)
        url_frame.pack(side="right", padx=14, pady=8)
        url_frame.pack_propagate(False)
        tk.Label(url_frame, text=WEB_URL,
                 font=FONT_MONO, bg=C["bg3"], fg=C["accent2"],
                 cursor="hand2").pack()
        url_frame.bind("<Button-1>", lambda _: self._do_open_browser())

    # ── Log pane ───────────────────────────────────────────────────────

    def _build_log_pane(self, parent):
        frame = tk.Frame(parent, bg=C["bg2"], bd=0)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        # Header row
        hdr = tk.Frame(frame, bg=C["bg2"])
        hdr.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(hdr, text="Server Log", font=FONT_HEAD,
                 bg=C["bg2"], fg=C["text2"]).pack(side="left")
        tk.Button(hdr, text="Clear", font=FONT_SMALL, bg=C["bg3"],
                  fg=C["text3"], relief="flat", bd=0, padx=8, pady=2,
                  cursor="hand2", command=self._clear_log,
                  activebackground=C["bg4"], activeforeground=C["text"]
                  ).pack(side="right")

        # Log widget
        self._log_box = scrolledtext.ScrolledText(
            frame, bg=C["bg0"], fg=C["text"],
            font=FONT_MONO,
            relief="flat", bd=0,
            insertbackground=C["text"],
            wrap="word", state="disabled",
            selectbackground=C["accent"], selectforeground="#fff",
        )
        self._log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._log_box.tag_config("green",  foreground=C["green"])
        self._log_box.tag_config("red",    foreground=C["red"])
        self._log_box.tag_config("yellow", foreground=C["yellow"])
        self._log_box.tag_config("cyan",   foreground=C["cyan"])
        self._log_box.tag_config("accent", foreground=C["accent2"])
        self._log_box.tag_config("muted",  foreground=C["text3"])

    # ── Info / status pane ─────────────────────────────────────────────

    def _build_info_pane(self, parent):
        frame = tk.Frame(parent, bg=C["bg1"])
        frame.grid(row=0, column=1, sticky="nsew")

        # Service status card
        self._build_card(frame, "Services", self._build_service_status_card)

        # Quick-start card
        self._build_card(frame, "Quick Start", self._build_quickstart_card)

        # Models card
        self._build_card(frame, "Installed Models", self._build_models_card)

        # Recommended card
        self._build_card(frame, "Recommended Models", self._build_recs_card)

    def _build_card(self, parent, title: str, builder):
        card = tk.Frame(parent, bg=C["bg2"])
        card.pack(fill="x", pady=(0, 6))
        tk.Label(card, text=title, font=FONT_HEAD,
                 bg=C["bg2"], fg=C["text2"]).pack(anchor="w", padx=12, pady=(10, 5))
        tk.Frame(card, bg=C["border"], height=1).pack(fill="x", padx=12)
        builder(card)

    def _build_service_status_card(self, parent):
        f = tk.Frame(parent, bg=C["bg2"])
        f.pack(fill="x", padx=12, pady=8)
        services = [
            ("Ollama",  "AI inference engine"),
            ("Server",  f"FastAPI  ·  port {WEB_PORT}"),
            ("Web UI",  WEB_URL),
        ]
        self._svc_rows = {}
        for name, desc in services:
            row = tk.Frame(f, bg=C["bg2"])
            row.pack(fill="x", pady=3)
            dot = tk.Label(row, text="●", font=("Segoe UI", 11),
                           bg=C["bg2"], fg=C["red"])
            dot.pack(side="left")
            col = tk.Frame(row, bg=C["bg2"])
            col.pack(side="left", padx=8)
            tk.Label(col, text=name, font=FONT_BOLD,
                     bg=C["bg2"], fg=C["text"]).pack(anchor="w")
            tk.Label(col, text=desc, font=FONT_SMALL,
                     bg=C["bg2"], fg=C["text3"]).pack(anchor="w")
            self._svc_rows[name] = dot

    def _build_quickstart_card(self, parent):
        f = tk.Frame(parent, bg=C["bg2"])
        f.pack(fill="x", padx=12, pady=8)
        steps = [
            ("1", "Click  ▶ Start All"),
            ("2", "Click  ⬡ Open UI"),
            ("3", "Set a target, start hacking"),
        ]
        for n, step in steps:
            row = tk.Frame(f, bg=C["bg2"])
            row.pack(fill="x", pady=2)
            badge = tk.Label(row, text=n, font=FONT_BOLD,
                             bg=C["accent"], fg="#fff",
                             width=2, anchor="center",
                             padx=4, pady=1)
            badge.pack(side="left")
            tk.Label(row, text=step, font=FONT_UI,
                     bg=C["bg2"], fg=C["text"]).pack(side="left", padx=8)

    def _build_models_card(self, parent):
        self._models_frame = tk.Frame(parent, bg=C["bg2"])
        self._models_frame.pack(fill="x", padx=12, pady=8)
        self._models_label = tk.Label(
            self._models_frame,
            text="(start Ollama to see models)",
            font=FONT_SMALL, bg=C["bg2"], fg=C["text3"],
            anchor="w", justify="left"
        )
        self._models_label.pack(anchor="w")

    def _build_recs_card(self, parent):
        f = tk.Frame(parent, bg=C["bg2"])
        f.pack(fill="x", padx=12, pady=8)
        recs = [
            ("dolphin-mistral",  "uncensored chat"),
            ("mistral",          "pentest reasoning"),
            ("deepseek-coder-v2","code + exploits"),
            ("nous-hermes2",     "multi-purpose"),
        ]
        for model, desc in recs:
            row = tk.Frame(f, bg=C["bg2"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=model, font=FONT_MONO,
                     bg=C["bg2"], fg=C["cyan"]).pack(side="left")
            tk.Label(row, text=f" — {desc}", font=FONT_SMALL,
                     bg=C["bg2"], fg=C["text3"]).pack(side="left")
            # Quick-pull on click
            row.bind("<Button-1>", lambda e, m=model: self._quick_pull(m))
            row.configure(cursor="hand2")

    # ── Status bar ─────────────────────────────────────────────────────

    def _build_statusbar(self):
        sb = tk.Frame(self, bg=C["bg0"], height=28)
        sb.pack(fill="x", side="bottom")
        sb.pack_propagate(False)
        self._status_var = tk.StringVar(value="  Ready — click ▶ Start All to begin")
        tk.Label(sb, textvariable=self._status_var,
                 font=FONT_SMALL, bg=C["bg0"], fg=C["text3"],
                 anchor="w").pack(side="left", fill="x", padx=8)
        tk.Label(sb, text=f"Vulntrix v{APP_VERSION}  ",
                 font=FONT_SMALL, bg=C["bg0"], fg=C["text3"]).pack(side="right")

    # ── Widget helpers ─────────────────────────────────────────────────

    def _tb_btn(self, parent, label, color, cmd, ghost=False):
        if ghost:
            btn = tk.Button(
                parent, text=label, font=FONT_UI,
                bg=C["bg3"], fg=C["text2"],
                relief="flat", bd=0, padx=12, pady=6,
                cursor="hand2", command=cmd,
                activebackground=C["bg4"], activeforeground=C["text"],
            )
        else:
            btn = tk.Button(
                parent, text=label, font=FONT_BOLD,
                bg=color, fg="#fff",
                relief="flat", bd=0, padx=14, pady=6,
                cursor="hand2", command=cmd,
                activebackground=color, activeforeground="#fff",
            )
        btn.pack(side="left", padx=3)
        return btn

    def _sep(self, parent):
        tk.Frame(parent, bg=C["border2"], width=1, height=28).pack(
            side="left", padx=6, pady=6
        )

    def _make_indicator(self, parent, label: str):
        frame = tk.Frame(parent, bg=C["bg0"], padx=4)
        frame.pack(side="left", padx=6)
        dot = tk.Label(frame, text="●", font=("Segoe UI", 10),
                       bg=C["bg0"], fg=C["red"])
        dot.pack(side="left")
        tk.Label(frame, text=label, font=FONT_SMALL,
                 bg=C["bg0"], fg=C["text3"]).pack(side="left", padx=3)
        return dot

    def _set_dot(self, dot, ok: bool):
        dot.configure(fg=C["green"] if ok else C["red"])

    # ══════════════════ ACTIONS ════════════════════════════════════════

    def _do_start_all(self):
        self._status("Starting services…")
        self.svc.start_all()

    def _do_stop_all(self):
        self._status("Stopping services…")
        threading.Thread(target=self.svc.stop_all, daemon=True).start()

    def _do_open_browser(self):
        webbrowser.open(WEB_URL)
        self._status(f"Opened {WEB_URL}")

    def _quick_pull(self, model: str):
        if messagebox.askyesno("Pull Model",
                               f"Pull  {model}  from Ollama registry?",
                               parent=self):
            self.svc.pull_model(model)
            self._status(f"Pulling {model}…")

    # ══════════════════ LOG ════════════════════════════════════════════

    def _append_log(self, text: str):
        self.after(0, self._log_insert, text)

    def _log_insert(self, text: str):
        self._log_box.configure(state="normal")
        lo = text.lower()
        tag = None
        if any(k in lo for k in ("error", "fail", "err:", "traceback", "exception")):
            tag = "red"
        elif any(k in lo for k in ("started", "ready", "✓", "success", "connected", "ok")):
            tag = "green"
        elif any(k in lo for k in ("warn", "⚠", "warning")):
            tag = "yellow"
        elif text.startswith("[server]"):
            tag = "cyan"
        elif text.startswith("[ollama]"):
            tag = "accent"
        elif text.startswith("[pull]"):
            tag = "muted"

        if tag:
            self._log_box.insert("end", text, tag)
        else:
            self._log_box.insert("end", text)

        # Keep log buffer from growing unbounded
        lines = int(self._log_box.index("end-1c").split(".")[0])
        if lines > 2000:
            self._log_box.delete("1.0", f"{lines - 1500}.0")

        self._log_box.configure(state="disabled")
        self._log_box.see("end")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ══════════════════ STATUS CALLBACKS ═══════════════════════════════

    def _on_status(self, ollama_ok: bool, server_ok: bool):
        self.after(0, self._apply_status, ollama_ok, server_ok)

    def _apply_status(self, ollama_ok: bool, server_ok: bool):
        self._set_dot(self._svc_rows["Ollama"], ollama_ok)
        self._set_dot(self._svc_rows["Server"], server_ok)
        self._set_dot(self._dot_ollama, ollama_ok)
        self._set_dot(self._dot_server, server_ok)

        # Check web UI reachability
        web_ok = self._check_web()
        self._set_dot(self._svc_rows["Web UI"], web_ok)
        self._set_dot(self._dot_web, web_ok)

        if ollama_ok and server_ok:
            self._status(f"All services running  ·  {WEB_URL}")
        elif server_ok:
            self._status("Web server running (Ollama offline)")
        elif ollama_ok:
            self._status("Ollama running, web server starting…")
        else:
            self._status("Services stopped  ·  click ▶ Start All")

        # Refresh models list
        if ollama_ok:
            models = self.svc.list_models()
            if models:
                self._models_label.configure(
                    text="\n".join(f"  • {m}" for m in models[:10]),
                    fg=C["text"]
                )
            else:
                self._models_label.configure(
                    text="  (none pulled yet — use ⬇ Pull Model)",
                    fg=C["text3"]
                )

    def _check_web(self) -> bool:
        try:
            import urllib.request
            with urllib.request.urlopen(WEB_URL, timeout=1) as r:
                return r.status < 500
        except Exception:
            return False

    def _poll_health(self):
        self._on_status(self.svc.ollama_running, self.svc.server_running)
        self.after(8000, self._poll_health)

    def _status(self, msg: str):
        self.after(0, lambda: self._status_var.set(f"  {msg}"))

    # ══════════════════ DIALOGS ════════════════════════════════════════

    def _show_pull_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Pull Ollama Model")
        dlg.configure(bg=C["bg1"])
        dlg.geometry("440x280")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_set()

        tk.Label(dlg, text="Pull a Model", font=FONT_TITLE,
                 bg=C["bg1"], fg=C["text"]).pack(pady=(20, 4))
        tk.Label(dlg, text="Enter model name or pick a preset below:",
                 font=FONT_SMALL, bg=C["bg1"], fg=C["text3"]).pack()

        # Entry
        ef = tk.Frame(dlg, bg=C["bg3"], padx=2, pady=2)
        ef.pack(pady=10, padx=30, fill="x")
        entry = tk.Entry(ef, bg=C["bg3"], fg=C["text"],
                         insertbackground=C["text"],
                         relief="flat", font=FONT_UI, bd=6)
        entry.pack(fill="x")
        entry.insert(0, "dolphin-mistral")
        entry.focus()

        # Progress label
        prog = tk.Label(dlg, text="", font=FONT_SMALL,
                        bg=C["bg1"], fg=C["yellow"], wraplength=380)
        prog.pack(pady=2)

        def do_pull():
            model = entry.get().strip()
            if not model:
                return
            prog.configure(text=f"Pulling {model}…", fg=C["yellow"])
            btn_pull.configure(state="disabled", text="Pulling…")
            self.svc.pull_model(model, progress_cb=lambda t: prog.configure(
                text=t[:80], fg=C["green"] if "✓" in t else C["yellow"]
            ))
            dlg.after(1200, dlg.destroy)

        btn_pull = tk.Button(dlg, text="Pull Model", font=FONT_BOLD,
                             bg=C["accent"], fg="#fff",
                             relief="flat", bd=0, padx=20, pady=8,
                             cursor="hand2", command=do_pull,
                             activebackground=C["accent2"])
        btn_pull.pack(pady=4)
        entry.bind("<Return>", lambda _: do_pull())

        # Quick-pick row
        qf = tk.Frame(dlg, bg=C["bg1"])
        qf.pack(pady=6)
        tk.Label(qf, text="Presets:", font=FONT_SMALL,
                 bg=C["bg1"], fg=C["text3"]).pack(side="left", padx=(0, 6))
        for m in ["dolphin-mistral", "mistral", "deepseek-coder-v2", "nous-hermes2"]:
            tk.Button(qf, text=m, font=FONT_SMALL,
                      bg=C["bg3"], fg=C["cyan"],
                      relief="flat", bd=0, padx=7, pady=4, cursor="hand2",
                      activebackground=C["bg4"],
                      command=lambda _m=m: (entry.delete(0, "end"), entry.insert(0, _m))
                      ).pack(side="left", padx=2)

    def _show_models(self):
        models = self.svc.list_models()
        dlg = tk.Toplevel(self)
        dlg.title("Installed Models")
        dlg.configure(bg=C["bg1"])
        dlg.geometry("380x320")
        dlg.grab_set()

        tk.Label(dlg, text="Installed Models", font=FONT_TITLE,
                 bg=C["bg1"], fg=C["text"]).pack(pady=(20, 10))

        if models:
            scroll_frame = tk.Frame(dlg, bg=C["bg2"])
            scroll_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))
            for m in models:
                row = tk.Frame(scroll_frame, bg=C["bg2"], pady=5, padx=12)
                row.pack(fill="x", pady=1)
                tk.Label(row, text="●", font=("Segoe UI", 9),
                         bg=C["bg2"], fg=C["green"]).pack(side="left")
                tk.Label(row, text=f"  {m}", font=FONT_MONO,
                         bg=C["bg2"], fg=C["text"]).pack(side="left")
        else:
            tk.Label(dlg,
                     text="No models installed yet.\n\nUse ⬇ Pull Model to download one.",
                     font=FONT_UI, bg=C["bg1"], fg=C["text3"],
                     justify="center").pack(pady=30)

        tk.Button(dlg, text="Close", font=FONT_UI,
                  bg=C["bg3"], fg=C["text"],
                  relief="flat", bd=0, padx=16, pady=7,
                  cursor="hand2", command=dlg.destroy,
                  activebackground=C["bg4"]).pack(pady=8)

    # ══════════════════ CLOSE / TRAY ═══════════════════════════════════

    def _on_close(self):
        try:
            self._minimize_to_tray()
        except Exception:
            self._quit_app()

    def _minimize_to_tray(self):
        try:
            import pystray
            from PIL import Image

            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, size-2, size-2], fill=(26, 22, 55, 255))
            draw.ellipse([2, 2, size-2, size-2], outline=(139, 108, 247, 255), width=3)
            # Use default font — avoids platform-specific font path issues
            draw.text((size//4, size//4), "V", fill=(200, 170, 255, 255))

            menu = pystray.Menu(
                pystray.MenuItem("Open UI",      lambda: self.after(0, self._do_open_browser)),
                pystray.MenuItem("Show Window",  lambda: self.after(0, self._show_from_tray)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Stop All",     lambda: self.after(0, self._do_stop_all)),
                pystray.MenuItem("Quit",         lambda: self.after(0, self._quit_app)),
            )
            self._tray = pystray.Icon("Vulntrix", img, "Vulntrix", menu)
            self.withdraw()
            threading.Thread(target=self._tray.run, daemon=True).start()

        except ImportError:
            self.withdraw()
            messagebox.showinfo(
                "Vulntrix minimised",
                "The app is still running in the background.\n"
                "Reopen from the taskbar or close this to quit.",
                parent=self
            )

    def _show_from_tray(self):
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_app(self):
        if messagebox.askyesno("Quit Vulntrix",
                               "Stop all services and quit?",
                               parent=self):
            if self._tray:
                self._tray.stop()
            self.svc.stop_all()
            self.destroy()

    # ── TTK styling ────────────────────────────────────────────────────

    def _apply_ttk_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame",     background=C["bg1"])
        style.configure("TLabel",     background=C["bg1"], foreground=C["text"])
        style.configure("TScrollbar", background=C["bg2"],
                        troughcolor=C["bg0"], arrowcolor=C["text3"])


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Suppress console window on Windows when launched via pythonw
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0
            )
        except Exception:
            pass

    app = App()
    app.mainloop()
