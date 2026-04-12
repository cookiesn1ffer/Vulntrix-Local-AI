@echo off
setlocal
cd /d "%~dp0"
echo.
echo   ==========================================
echo     Vulntrix -- Starting...
echo   ==========================================
echo.

:: ── Python interpreter ───────────────────────────────────────────────────────
if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else if exist "venv\Scripts\python.exe" (
    set PY=venv\Scripts\python.exe
) else if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" (
    set PY=py -3
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

:: ── Ollama ────────────────────────────────────────────────────────────────────
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

:: ── TLS detection — web_server.py auto-picks port/scheme ─────────────────────
::   certs\server.crt + server.key present  ->  HTTPS on 8443
::   no certs                               ->  HTTP  on 8000
set PORT=8000
set SCHEME=http

if exist "certs\server.crt" if exist "certs\server.key" (
    set PORT=8443
    set SCHEME=https
    echo [OK] TLS certs found -- using https://localhost:8443
) else (
    echo [*] No TLS certs -- using http://localhost:8000
)

:: Allow manual port override: set VULNTRIX_PORT=XXXX before running
if not "%VULNTRIX_PORT%"=="" set PORT=%VULNTRIX_PORT%

:: Open browser after server has had time to bind (~2 s)
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start %SCHEME%://localhost:%PORT%"

echo [*] Starting Vulntrix server... (Ctrl+C to stop)
echo.
%PY% web_server.py
