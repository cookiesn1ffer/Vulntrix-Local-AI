@echo off
setlocal
cd /d "%~dp0"
echo Starting Vulntrix...

tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if ERRORLEVEL 1 (
    where ollama >NUL 2>&1
    if ERRORLEVEL 1 (
        echo [WARN] Ollama not found in PATH. Install from https://ollama.com
    ) else (
        echo Starting Ollama...
        start /B ollama serve
        timeout /t 3 /nobreak >nul
    )
)

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" (
    set PY=py -3
) else (
    set PY=python
)

if "%PORT%"=="" set PORT=8000
start "" "http://localhost:%PORT%"
%PY% web_server.py
