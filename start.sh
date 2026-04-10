#!/usr/bin/env bash
# Vulntrix — Quick Launcher (Linux / macOS)
# ─────────────────────────────────────────
# Usage:
#   ./start.sh               — plain HTTP on port 8000
#   ./start.sh --tls         — HTTPS on port 8443  (auto-generates cert if missing)
#   ./start.sh --tray        — launch with system-tray icon (requires pystray)
#   ./start.sh --no-browser  — skip auto-opening the browser

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# ── Colour helpers ────────────────────────────────────────────────────────────
GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'
RST='\033[0m';    BLD='\033[1m'
info() { echo -e "${CYN}[*]${RST} $*"; }
ok()   { echo -e "${GRN}[✓]${RST} $*"; }
warn() { echo -e "${YLW}[!]${RST} $*"; }

# ── Arguments ────────────────────────────────────────────────────────────────
USE_TLS=false; USE_TRAY=false; OPEN_BROWSER=true
for arg in "$@"; do
    case "$arg" in
        --tls)        USE_TLS=true ;;
        --tray)       USE_TRAY=true ;;
        --no-browser) OPEN_BROWSER=false ;;
    esac
done

# ── Pick Python (venv preferred, then system) ────────────────────────────────
VENV_PY="$DIR/.venv/bin/python"
if [ -f "$VENV_PY" ]; then
    PY="$VENV_PY"
elif command -v python3 &>/dev/null; then
    PY="python3"
elif command -v python &>/dev/null; then
    PY="python"
else
    echo "ERROR: Python 3 not found. Run install.sh first." >&2; exit 1
fi

# Warn if Python < 3.10
PY_VER=$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
PY_MINOR=$("$PY" -c 'import sys; print(sys.version_info[1])')
PY_MAJOR=$("$PY" -c 'import sys; print(sys.version_info[0])')
[[ "$PY_MAJOR" -lt 3 || "$PY_MINOR" -lt 10 ]] && warn "Python 3.10+ recommended (got $PY_VER)"

# ── TLS setup ────────────────────────────────────────────────────────────────
CERT="$DIR/certs/server.crt"; KEY="$DIR/certs/server.key"
if $USE_TLS || [[ -f "$CERT" && -f "$KEY" ]]; then
    if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
        warn "No cert found — generating self-signed certificate..."
        "$PY" "$DIR/scripts/gen_cert.py"
    fi
    ok "TLS enabled — HTTPS mode"
    export PORT="${PORT:-8443}"; SCHEME="https"
else
    export PORT="${PORT:-8000}"; SCHEME="http"
fi
URL="${SCHEME}://localhost:${PORT}"

# ── Ollama ───────────────────────────────────────────────────────────────────
if ! pgrep -x ollama &>/dev/null 2>&1; then
    if command -v ollama &>/dev/null; then
        info "Starting Ollama..."
        ollama serve > /tmp/vulntrix-ollama.log 2>&1 &
        sleep 2; ok "Ollama started"
    else
        warn "Ollama not found — install from https://ollama.com"
    fi
else
    ok "Ollama already running"
fi

# ── Banner ───────────────────────────────────────────────────────────────────
echo -e "\n${BLD}${CYN}  ╔══════════════════════════════════╗"
echo -e        "  ║      Vulntrix v3.0               ║"
echo -e        "  ╚══════════════════════════════════╝${RST}"
echo -e "  ${BLD}Web UI →${RST} ${YLW}${URL}${RST}\n"

# ── Launch ───────────────────────────────────────────────────────────────────
if $USE_TRAY; then
    info "Starting with system-tray icon..."
    exec "$PY" "$DIR/tray.py"
else
    if $OPEN_BROWSER; then
        # Try xdg-open (Linux) → open (macOS) → fallback silently
        ( sleep 2 && { xdg-open "$URL" 2>/dev/null || open "$URL" 2>/dev/null || true; } ) &
    fi
    info "Starting server (Ctrl+C to stop)..."
    exec "$PY" "$DIR/web_server.py"
fi
