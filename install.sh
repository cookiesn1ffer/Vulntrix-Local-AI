#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  Vulntrix — Linux / macOS One-Click Installer
#  Supports: Ubuntu/Debian, Arch, Fedora/RHEL, macOS (Homebrew)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
BLU='\033[0;34m'; MAG='\033[0;35m'; CYN='\033[0;36m'
BLD='\033[1m';    RST='\033[0m'

info()  { echo -e "${BLU}[INFO]${RST}  $*"; }
ok()    { echo -e "${GRN}[ OK ]${RST}  $*"; }
warn()  { echo -e "${YLW}[WARN]${RST}  $*"; }
err()   { echo -e "${RED}[ERR ]${RST}  $*" >&2; exit 1; }
step()  { echo -e "\n${MAG}${BLD}▶ $*${RST}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Banner ─────────────────────────────────────────────────────────
echo -e "${CYN}${BLD}"
cat << 'BANNER'
  ██╗   ██╗██╗   ██╗██╗     ███╗   ██╗████████╗██████╗ ██╗██╗  ██╗
  ██║   ██║██║   ██║██║     ████╗  ██║╚══██╔══╝██╔══██╗██║╚██╗██╔╝
  ██║   ██║██║   ██║██║     ██╔██╗ ██║   ██║   ██████╔╝██║ ╚███╔╝
  ╚██╗ ██╔╝██║   ██║██║     ██║╚██╗██║   ██║   ██╔══██╗██║ ██╔██╗
   ╚████╔╝ ╚██████╔╝███████╗██║ ╚████║   ██║   ██║  ██║██║██╔╝ ██╗
    ╚═══╝   ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═╝
  Local AI Pentest Suite — Private, Offline, Uncensored
BANNER
echo -e "${RST}"

# ── Detect OS / Distro ─────────────────────────────────────────────
step "Detecting operating system"

OS="$(uname -s)"
DISTRO="unknown"; PKG=""

if [[ "$OS" == "Darwin" ]]; then
    DISTRO="macos"
    command -v brew &>/dev/null && PKG="brew" || warn "Homebrew not found — some installs may fail"
elif [[ "$OS" == "Linux" ]]; then
    if   command -v pacman  &>/dev/null; then DISTRO="arch";   PKG="pacman"
    elif command -v apt-get &>/dev/null; then DISTRO="debian";  PKG="apt"
    elif command -v dnf     &>/dev/null; then DISTRO="fedora";  PKG="dnf"
    elif command -v yum     &>/dev/null; then DISTRO="rhel";    PKG="yum"
    else warn "Unknown Linux distro — continuing without package manager"
    fi
else
    err "Unsupported OS: $OS. Use install.ps1 on Windows."
fi

info "OS: $OS / Distro: $DISTRO (pkg: ${PKG:-none})"
info "Install directory: $SCRIPT_DIR"

# ── Python 3.10+ ───────────────────────────────────────────────────
step "Checking Python 3.10+"

PY=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        _maj=$("$cmd" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)
        _min=$("$cmd" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)
        if [[ "$_maj" -ge 3 && "$_min" -ge 10 ]]; then
            PY="$cmd"; break
        fi
    fi
done

if [[ -z "$PY" ]]; then
    warn "Python 3.10+ not found — attempting install..."
    case "$DISTRO" in
        arch)   sudo pacman -Sy --noconfirm python ;;
        debian) sudo apt-get update -qq && sudo apt-get install -y python3 python3-venv python3-pip ;;
        fedora) sudo dnf install -y python3 python3-pip ;;
        rhel)   sudo yum install -y python3 python3-pip ;;
        macos)  brew install python@3.11 ;;
        *)      err "Please install Python 3.10+ manually and re-run." ;;
    esac
    PY=python3
fi
ok "Python: $($PY --version)"

# ── Virtual environment ────────────────────────────────────────────
step "Setting up virtual environment"

VENV="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV" ]]; then
    "$PY" -m venv "$VENV" 2>/dev/null || {
        case "$DISTRO" in
            debian) sudo apt-get install -y python3-venv ;;
        esac
        "$PY" -m venv "$VENV"
    }
    ok "venv created at $VENV"
else
    ok "venv already exists"
fi

PY_VENV="$VENV/bin/python"
PIP="$VENV/bin/pip"
"$PIP" install --upgrade pip --quiet

# ── Tkinter (system package) ───────────────────────────────────────
step "Checking Tkinter"

if ! "$PY_VENV" -c "import tkinter" &>/dev/null 2>&1; then
    warn "Tkinter not found — installing..."
    case "$DISTRO" in
        arch)   sudo pacman -Sy --noconfirm tk ;;
        debian) sudo apt-get install -y python3-tk ;;
        fedora) sudo dnf install -y python3-tkinter ;;
        rhel)   sudo yum install -y python3-tkinter ;;
        macos)  warn "Run: brew install python-tk" ;;
        *)      warn "Install tkinter manually" ;;
    esac
else
    ok "Tkinter ready"
fi

# ── System tray dependencies (Linux only) ─────────────────────────
if [[ "$OS" == "Linux" ]]; then
    step "Checking system-tray dependencies"
    # pystray needs libappindicator or gtk tray support on Linux
    TRAY_OK=false
    case "$DISTRO" in
        arch)
            if ! pacman -Qs python-gobject &>/dev/null; then
                info "Installing GTK/AppIndicator for system tray..."
                sudo pacman -Sy --noconfirm python-gobject libappindicator-gtk3 2>/dev/null || \
                    warn "Could not install tray libs — tray.py will fall back gracefully"
            fi
            TRAY_OK=true ;;
        debian)
            if ! dpkg -l python3-gi &>/dev/null 2>&1; then
                info "Installing GTK/AppIndicator for system tray..."
                sudo apt-get install -y python3-gi gir1.2-appindicator3-0.1 \
                    gir1.2-gtk-3.0 2>/dev/null || \
                    warn "Could not install tray libs — tray.py will fall back gracefully"
            fi
            TRAY_OK=true ;;
        fedora)
            sudo dnf install -y python3-gobject libappindicator-gtk3 2>/dev/null || \
                warn "Could not install tray libs"
            TRAY_OK=true ;;
        *)
            warn "Unknown distro — system tray may not work. Install python-gobject + libappindicator-gtk3 manually." ;;
    esac
    $TRAY_OK && ok "System tray libs ready"
fi

# ── Python packages ────────────────────────────────────────────────
step "Installing Python packages"

"$PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt"
ok "All Python packages installed"

# ── Ollama ─────────────────────────────────────────────────────────
step "Checking Ollama"

if command -v ollama &>/dev/null; then
    ok "Ollama already installed: $(ollama --version 2>/dev/null | head -1)"
else
    info "Installing Ollama..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install ollama 2>/dev/null || curl -fsSL https://ollama.com/install.sh | sh
    else
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    ok "Ollama installed"
fi

# ── Pull AI models ─────────────────────────────────────────────────
step "Pulling AI models"

echo -e "  ${YLW}Which models do you want to pull?${RST}"
echo -e "  ${BLD}1)${RST} mistral            ~4 GB  — pentest reasoning"
echo -e "  ${BLD}2)${RST} deepseek-coder      ~2 GB  — exploit / code generation"
echo -e "  ${BLD}3)${RST} dolphin-mistral     ~4 GB  — uncensored general chat"
echo -e "  ${BLD}4)${RST} All of the above"
echo -e "  ${BLD}5)${RST} Skip (pull later inside the app)"
echo ""
read -rp "  Choice [4]: " MODEL_CHOICE
MODEL_CHOICE="${MODEL_CHOICE:-4}"

_pull() {
    info "Pulling $1..."
    ollama pull "$1" && ok "$1 ready" || warn "Failed to pull $1 — try later"
}

if ! pgrep -x ollama &>/dev/null; then
    info "Starting Ollama temporarily..."
    ollama serve > /tmp/vulntrix-ollama-install.log 2>&1 &
    sleep 3
fi

case "$MODEL_CHOICE" in
    1) _pull mistral ;;
    2) _pull deepseek-coder ;;
    3) _pull dolphin-mistral ;;
    4) _pull mistral; _pull deepseek-coder; _pull dolphin-mistral ;;
    *) info "Skipping model pull." ;;
esac

# ── TLS (optional) ────────────────────────────────────────────────
step "TLS setup (optional)"

echo ""
read -rp "  Generate a self-signed HTTPS certificate? [y/N]: " DO_TLS
if [[ "${DO_TLS,,}" == "y" ]]; then
    "$PY_VENV" "$SCRIPT_DIR/scripts/gen_cert.py"
    ok "TLS cert generated — server will start on https://localhost:8443"
else
    info "Skipping TLS — server will use http://localhost:8000"
fi

# ── Desktop shortcut ──────────────────────────────────────────────
step "Creating desktop shortcut"

APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
ICON="$SCRIPT_DIR/web_ui/logo.svg"

DESKTOP_FILE="$APPS_DIR/vulntrix.desktop"
sed "s|INSTALL_DIR|$SCRIPT_DIR|g" "$SCRIPT_DIR/linux/vulntrix.desktop" > "$DESKTOP_FILE"
chmod +x "$DESKTOP_FILE"

command -v update-desktop-database &>/dev/null && \
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
ok "Desktop shortcut created: $DESKTOP_FILE"

# ── Systemd user service ───────────────────────────────────────────
step "Systemd auto-start (optional)"

if command -v systemctl &>/dev/null && systemctl --user status &>/dev/null 2>&1; then
    echo ""
    read -rp "  Enable auto-start on login via systemd? [y/N]: " DO_SYSTEMD
    if [[ "${DO_SYSTEMD,,}" == "y" ]]; then
        SERVICE_DIR="$HOME/.config/systemd/user"
        mkdir -p "$SERVICE_DIR"
        sed "s|INSTALL_DIR|$SCRIPT_DIR|g" \
            "$SCRIPT_DIR/linux/vulntrix.service" > "$SERVICE_DIR/vulntrix.service"
        # Replace INSTALL_DIR/.venv/bin/python with actual venv path (portable: GNU/BSD sed).
        _svc_tmp="${SERVICE_DIR}/.vulntrix.service.tmp"
        sed "s|INSTALL_DIR/.venv/bin/python|$VENV/bin/python|g" "$SERVICE_DIR/vulntrix.service" > "$_svc_tmp"
        mv "$_svc_tmp" "$SERVICE_DIR/vulntrix.service"
        systemctl --user daemon-reload
        systemctl --user enable vulntrix.service
        ok "vulntrix.service enabled — starts automatically on next login"
        info "Start now: systemctl --user start vulntrix"
    else
        info "Skipping systemd setup"
    fi
else
    info "systemd not available — skipping auto-start"
fi

# ── Summary ────────────────────────────────────────────────────────
URL="http://localhost:8000"
[[ -f "$SCRIPT_DIR/certs/server.crt" ]] && URL="https://localhost:8443"

echo ""
echo -e "${GRN}${BLD}══════════════════════════════════════════${RST}"
echo -e "${GRN}${BLD}  ✅  Installation complete!${RST}"
echo -e "${GRN}${BLD}══════════════════════════════════════════${RST}"
echo ""
echo -e "  ${BLD}Launch:${RST}"
echo -e "  ${CYN}1.${RST} Application menu → Vulntrix"
echo -e "  ${CYN}2.${RST} Terminal:   ${YLW}./start.sh${RST}"
echo -e "  ${CYN}3.${RST} Tray icon:  ${YLW}./start.sh --tray${RST}"
echo ""
echo -e "  ${BLD}Web UI:${RST} ${CYN}${URL}${RST}"
echo ""
echo -e "  ${BLD}Optional — generate TLS cert later:${RST}"
echo -e "  ${YLW}  python scripts/gen_cert.py${RST}"
echo ""
echo -e "  ${BLD}Pull more models anytime:${RST}"
echo -e "  ${YLW}  ollama pull dolphin-mistral${RST}  ← uncensored"
echo -e "  ${YLW}  ollama pull llama3${RST}            ← general purpose"
echo ""

read -rp "  Launch Vulntrix now? [Y/n]: " LAUNCH_NOW
if [[ "${LAUNCH_NOW,,}" != "n" ]]; then
    exec "$SCRIPT_DIR/start.sh"
fi
