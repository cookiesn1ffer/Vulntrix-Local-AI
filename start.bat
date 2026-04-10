@echo off
setlocal
cd /d "%~dp0"
echo.
echo   ==========================================
echo     Vulntrix — Starting...
echo   ==========================================
echo.

:: ── Python interpreter ──────────────────────────────────────────────────────
if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else if exist "venv\Scripts\python.exe" (
    set PY=venv\Scripts\python.exe
) else (
    where python >NUL 2>&1
    if ERRORLEVEL 1 (
        echo [ERROR] Python not found. Install from https://python.org
        pause
        exit /b 1
    )
    set PY=python
)
echo [OK] Python: %PY%

:: ── Ollama ───────────────────────────────────────────────────────────────────
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if ERRORLEVEL 1 (
    where ollama >NUL 2>&1
    if ERRORLEVEL 1 (
        echo [WARN] Ollama not found. Install from https://ollama.com
    ) else (
        echo [*] Starting Ollama...
        start /B ollama serve
        timeout /t 3 /nobreak >nul
        echo [OK] Ollama started
    )
) else (
    echo [OK] Ollama already running
)

:: ── TLS detection — let web_server.py pick the right port/scheme ─────────────
:: Do NOT force PORT here. web_server.py auto-detects:
::   certs\server.crt + server.key present  →  HTTPS on 8443
::   no certs                               →  HTTP  on 8000
:: Reading that decision back so we open the right URL in the browser.
set PORT_HTTP=8000
set PORT_HTTPS=8443
set SCHEME=http
set PORT=%PORT_HTTP%

if exist "certs\server.crt" if exist "certs\server.key" (
    set SCHEME=https
    set PORT=%PORT_HTTPS%
    echo [OK] TLS certs found — will use https://localhost:%PORT_HTTPS%
) else (
    echo [*] No TLS certs — will use http://localhost:%PORT_HTTP%
)

:: Allow manual override: set PORT=XXXX before running the bat
if not "%VULNTRIX_PORT%"=="" set PORT=%VULNTRIX_PORT%

:: Open browser after a short delay (server needs ~2 s to bind)
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start %SCHEME%://localhost:%PORT%"

echo [*] Starting Vulntrix server... (Ctrl+C to stop)
echo.
%PY% web_server.py
