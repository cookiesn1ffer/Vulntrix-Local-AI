# Vulntrix v3.0

> Local, offline AI-powered penetration testing suite.  
> Powered by Ollama — nothing leaves your machine. No API keys. No filters. No internet required.

---

## Quick start

### Linux / macOS
```bash
git clone https://github.com/cookiesn1ffer/Vulntrix-Local-AI.git && cd Vulntrix-Local-AI
chmod +x install.sh && ./install.sh   # one-time setup
./start.sh                             # plain HTTP on port 8000
./start.sh --tls                       # HTTPS on port 8443 (auto-generates cert)
./start.sh --tray                      # system-tray icon, server runs in background
```

### Windows
```powershell
# Run from an elevated PowerShell:
Set-ExecutionPolicy Bypass -Scope Process -Force
.\install.ps1     # one-time setup (creates venv, installs deps, pulls models)
```
```
start.bat         ← run server  (HTTP :8000 or HTTPS :8443 if certs/ present)
run-hidden.vbs    ← tray icon, no CMD window
```

> **Note:** The installer correctly resolves the Desktop path on OneDrive-synced accounts.  
> Re-run `install.ps1` any time to repair shortcuts or re-pull models.

### Docker
```bash
docker build -t vulntrix .
docker run -p 8000:8000 -e BOT_SECRET=changeme vulntrix

# With TLS:
docker run -p 8443:8443 \
  -v /path/to/certs:/app/certs:ro \
  -e BOT_SECRET=changeme vulntrix
```

---

## Features

| Tab | What it does |
|-----|-------------|
| 💬 Pentest Chat | AI with full target context. `/code`, `/exploit`, `/analyse` commands |
| 🤖 AI Chat | Uncensored raw chat — editable system prompt, temperature control |
| 📡 Recon Analyser | Upload/paste nmap · gobuster · linpeas · ffuf. Anti-hallucination pipeline |
| 💥 Exploit Gen | Complete working exploits for 16+ vuln types in any language |
| 🔐 Hash Cracker | Identify hash type → exact hashcat/john commands → crack-time estimate |
| 🎭 Payload Obfuscator | Base64 · XOR · char · hex · PowerShell encode to bypass AV/IDS/WAF |
| 🔎 CVE Lookup | Software + version → structured CVE cards with CVSS, exploit path, patch |
| 🐉 MSF Modules | Vuln/service → exact Metasploit `use` commands with pre-filled options |
| 🛡 WAF Evasion | Bypass techniques for Cloudflare, ModSecurity, AWS WAF, F5, Snort |
| 🕵 Post-Exploitation | OS + access level → complete categorised command set |
| ⬆ PrivEsc Checklist | Interactive Linux/Windows checklist — tick off as you go |
| 📋 Wordlist Gen | Company/domain/keywords → 50+ targeted passwords, dirs, usernames |
| 🎣 Phishing | Email templates, pretexting scripts, infrastructure setup |
| 📝 Notes | Per-target persistent notes with labels |
| 🔑 Credentials | Store found creds, one-click hash cracking |
| ⛓ Attack Chain | Kill-chain stage tracker with status and presets |
| 🕐 Timeline | Chronological engagement log, filterable by category |
| 📄 Report | AI-generated pentest report → export as Markdown or PDF |

---

## What's new in v3.0

**Backend**
- `/api/version` endpoint — UI now shows live version badge pulled from the server.
- Improved JSON extraction: strips markdown code fences, falls back to bare objects, handles more AI output formats.
- File upload parsing (`/api/recon/file`) now runs in a thread executor — server stays responsive while parsing large scan files.
- `ollama_client`: retries on 5xx HTTP errors (503 service unavailable, etc.), not just connection errors. Error messages now include the model name for easier debugging.
- `session_store`: `set_current`/`get_current` are now thread-safe with a `threading.Lock`.
- `target_context`: log rotation caps at 500 entries, notes capped at 200 labels, credentials at 100 — prevents unbounded disk growth. Corrupt JSON files now log a warning instead of silently resetting.

**Frontend**
- Version badge displayed next to the Vulntrix title in the sidebar (fetched live from `/api/version`).
- Stop button in both chat panels — click to abort the current streaming response immediately.
- ARIA labels added to key interactive elements for better accessibility.
- `<br>` literal normalisation extended to handle HTML-entity-encoded variants emitted by some model families.
- Chat streaming stabilised: plain-text during stream, full markdown render on completion.

**Windows fixes**
- `install.ps1` — Desktop shortcut path now resolved via `[Environment]::GetFolderPath("Desktop")` so it works correctly on OneDrive-synced Desktops (previously crashed with a path-not-found error).
- `install.ps1` — pip upgrade inside the venv now uses `python -m pip install --upgrade pip` (the correct method; calling `pip.exe` directly to upgrade itself fails on Windows).
- `web_server.py` — `WindowsSelectorEventLoopPolicy` applied before `uvicorn.run` on Windows, eliminating the `ConnectionResetError: [WinError 10054]` spam in the console caused by the default `ProactorEventLoop` on TLS connections.

**AI output formatting**
- System prompts now include explicit Markdown-only formatting rules — models no longer emit raw `<br>` tags or use single-backtick spans for multi-line commands.

**Fixes carried from v2.x**
- Browser cache-busting for `app.js` and `index.html` — no more stale JS after updates.
- File upload 401 — missing auth header on the raw fetch call is now included.
- CVE Lookup "Ask AI" button now switches to the chat tab and sends the message automatically.
- Stop All button in the desktop app (Windows) uses `taskkill /F /T` to kill the entire uvicorn process tree.
- `start.bat` TLS port detection fixed — no longer hardcodes port 8000 when TLS certs are present.

---

## Repository

- GitHub: [https://github.com/cookiesn1ffer/Vulntrix-Local-AI](https://github.com/cookiesn1ffer/Vulntrix-Local-AI)

---

## Data storage and wipe

Vulntrix stores data locally on your machine:

- Server-side target data: `~/.vulntrix/targets/*.json` (notes, credentials, timeline/log, attack chain, analysis cache).
- Current target pointer: `~/.vulntrix/current_target`.
- Browser-side state: `sessionStorage` (`bot_token`) and `localStorage` (active tab + sidebar state).

To wipe all saved local pentest data, use the sidebar button:

- `Wipe Local Data` (next to `Lock`).
- This calls `/api/reset-data` and permanently deletes all saved target files.
- Browser state is cleared and the UI reloads after reset.

---

## Authentication (optional)

Copy `.env.example` to `.env` and set a password:

```ini
BOT_SECRET=your-strong-password-here
SESSION_TTL_HOURS=8
```

When auth is enabled the browser shows a login screen. A **UUID session token** is returned on login and stored in `sessionStorage` — your raw password is never kept in the browser. Sessions expire after `SESSION_TTL_HOURS` and can be revoked with the 🔒 Lock button in the sidebar.

---

## TLS / HTTPS

```bash
python scripts/gen_cert.py      # generates certs/server.key + certs/server.crt
./start.sh                      # auto-detects certs/ → switches to https://localhost:8443
```

The cert is self-signed — click *"Advanced → Proceed anyway"* once in your browser.  
For a trusted cert, replace `certs/server.key` / `certs/server.crt` with a [Let's Encrypt](https://letsencrypt.org/) or [mkcert](https://github.com/FiloSottile/mkcert) cert.

---

## Security hardening

| Layer | Detail |
|-------|--------|
| Rate limiting | 60 req/min global · 5 req/min on `/api/auth/verify` · 10 WS handshakes/min |
| Security headers | CSP · `X-Frame-Options: DENY` · nosniff · HSTS · `Referrer-Policy: no-referrer` |
| File locking | `fcntl.flock` on POSIX / `.lock` sidecar on Windows + atomic temp-file writes |
| Body size cap | 4 MB default (set `MAX_BODY_MB=N` in `.env` to change) |
| Session tokens | UUID sessions with configurable TTL — raw secret never stored in browser |
| Log sanitisation | `?token=***` stripped from uvicorn access logs automatically |
| CORS | Locked to `localhost` origins only |

---

## Anti-hallucination system

The nmap parser uses a 3-layer pipeline before the AI sees any data:

1. **Noise filter** — strips 18 patterns (`adjust_timeouts2`, NSOCK, timing lines, etc.)
2. **Strict validation** — only accepts lines matching `^\d+/(tcp|udp)\s+open\s+`
3. **Quality gate** — `Empty`/`LOW` quality scans constrain the AI to answer "Insufficient data" — it cannot invent services or ports

---

## Linux system tray

```bash
# Install tray dependencies:
# Ubuntu/Debian:  sudo apt install python3-gi gir1.2-appindicator3-0.1
# Arch:           sudo pacman -S python-gobject libappindicator-gtk3
# Fedora:         sudo dnf install python3-gobject libappindicator-gtk3

./start.sh --tray
```

`install.sh` installs these automatically and registers a `.desktop` file so Vulntrix appears in your app launcher. A systemd user service (`linux/vulntrix.service`) can be enabled for auto-start on login.

---

## Running tests

```bash
pytest tests/ -v
```

Windows (project venv):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

| File | What it covers |
|------|---------------|
| `test_parsers.py` | nmap, gobuster, linpeas parsers |
| `test_context.py` | TargetContext CRUD, file locking, persistence |
| `test_auth.py` | Session token create/verify/revoke/expiry |
| `test_endpoints.py` | FastAPI routes (auth, CRUD, rate limit, security headers) |

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_SECRET` | *(empty)* | Enable auth — set a strong password |
| `SESSION_TTL_HOURS` | `8` | Session expiry in hours |
| `PORT` | `8000` / `8443` | Override server port (auto-set by TLS detection) |
| `MAX_BODY_MB` | `4` | Max request body in megabytes |
| `RATE_LIMIT_REQUESTS` | `60` | Max REST req/window per IP |
| `RATE_LIMIT_WINDOW` | `60` | Rate-limit window in seconds |
| `RATE_LIMIT_WS` | `10` | Max new WebSocket connections/min per IP |
| `VULNTRIX_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `VULNTRIX_REASONING_MODEL` | *(auto-detected)* | Override reasoning model |
| `VULNTRIX_CODING_MODEL` | *(auto-detected)* | Override coding model |
| `VULNTRIX_LHOST` | *(empty)* | Default attacker IP for payload generation |
| `VULNTRIX_LPORT` | `4444` | Default listener port |

See `.env.example` for a fully commented template.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally
- Recommended models: `mistral`, `deepseek-coder`, `dolphin-mistral`
- **Linux tray:** `python3-gi` + `libappindicator-gtk3` (installed by `install.sh`)
- **TLS cert gen:** `cryptography` package (included in `requirements.txt`)

---

## Disclaimer

For authorised security testing and educational use only.  
Never use against systems you do not have explicit written permission to test.
