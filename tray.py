#!/usr/bin/env python3
"""
tray.py — System-tray launcher for Vulntrix.

Windows : Double-click run-hidden.vbs  (no console window)
Linux   : python tray.py  (requires libappindicator or gtk tray support)
macOS   : python tray.py

Right-click the tray icon → Open Browser / Stop Server.

Linux tray dependencies:
  Ubuntu/Debian : sudo apt install python3-gi gir1.2-appindicator3-0.1
  Arch          : sudo pacman -S python-gobject libappindicator-gtk3
  Fedora        : sudo dnf install python3-gobject libappindicator-gtk3
"""

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── Detect TLS so the URL is correct ─────────────────────────────────────────
_CERT   = HERE / "certs" / "server.crt"
_TLS    = _CERT.exists()
_PORT   = int(os.environ.get("PORT", "8443" if _TLS else "8000"))
_SCHEME = "https" if _TLS else "http"
WEB_URL = f"{_SCHEME}://localhost:{_PORT}"

# ── Imports that need the venv ────────────────────────────────────────────────
try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    _msg = (
        "Missing dependencies: pystray, Pillow\n\n"
        "Install with:\n  pip install pystray Pillow\n"
    )
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, _msg, "Vulntrix", 0x10)
        except Exception:
            print(_msg, file=sys.stderr)
    else:
        print(_msg, file=sys.stderr)
    sys.exit(1)


# ── Icon builder ──────────────────────────────────────────────────────────────

def make_icon() -> Image.Image:
    """Draw the Vulntrix 'V' logo as a 64×64 RGBA tray icon."""
    sz     = 64
    margin = 4
    img    = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d      = ImageDraw.Draw(img)

    # Indigo background blob
    d.rounded_rectangle(
        [margin, margin, sz - margin, sz - margin],
        radius=18,
        fill=(99, 102, 241, 255),
    )

    # Cyan shimmer top-left
    for i in range(16):
        alpha = int(120 * (1 - i / 16))
        d.ellipse(
            [margin + i, margin + i,
             sz // 2 + 4 - i, sz // 2 + 4 - i],
            fill=(125, 211, 252, alpha),
        )

    # White "V" strokes
    cx, lw = sz // 2, 4
    d.line([(14, 16), (cx, sz - 16)], fill=(255, 255, 255, 255), width=lw)
    d.line([(sz - 14, 16), (cx, sz - 16)], fill=(255, 255, 255, 255), width=lw)

    return img


# ── Start web server as hidden subprocess ────────────────────────────────────

def _start_server() -> subprocess.Popen:
    kwargs: dict = {
        "cwd": str(HERE),
    }

    if sys.platform == "win32":
        # No console window on Windows
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        # On Linux/macOS detach from terminal so closing terminal doesn't kill server
        kwargs["start_new_session"] = True
        # Silence server stdout/stderr in tray mode (logs go to logs/app.log)
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL

    return subprocess.Popen([sys.executable, str(HERE / "web_server.py")], **kwargs)


server_proc = _start_server()


# ── Wait until server is ready ───────────────────────────────────────────────

def _wait_for_server(timeout: int = 30) -> bool:
    import urllib.request
    import ssl as _ssl

    # Self-signed cert — skip verification for health check
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = _ssl.CERT_NONE

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            url = f"{WEB_URL}/api/health"
            urllib.request.urlopen(url, timeout=1, context=ctx if _TLS else None)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _open_browser_when_ready():
    if _wait_for_server():
        webbrowser.open(WEB_URL)


threading.Thread(target=_open_browser_when_ready, daemon=True).start()


# ── Tray callbacks ────────────────────────────────────────────────────────────

def on_open(icon, item):
    webbrowser.open(WEB_URL)


def on_stop(icon, item):
    try:
        icon.notify("Stopping Vulntrix…")
    except Exception:
        pass
    server_proc.terminate()
    try:
        server_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_proc.kill()
    icon.stop()


# ── Build and run tray icon ───────────────────────────────────────────────────

tray_icon = pystray.Icon(
    name  = "Vulntrix",
    icon  = make_icon(),
    title = f"Vulntrix  •  {WEB_URL}",
    menu  = pystray.Menu(
        pystray.MenuItem("Open Browser", on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop Server",  on_stop),
    ),
)

tray_icon.run()
