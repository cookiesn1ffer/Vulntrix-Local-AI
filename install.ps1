# =============================================================
#  Vulntrix -- Windows One-Click Installer
#  Run from PowerShell as Admin:
#    Set-ExecutionPolicy Bypass -Scope Process -Force
#    .\install.ps1
# =============================================================
#Requires -Version 5.1

$ErrorActionPreference = "Stop"

function Info  { param($m); Write-Host "  [INFO]  $m" -ForegroundColor Cyan }
function Ok    { param($m); Write-Host "  [ OK ]  $m" -ForegroundColor Green }
function Warn  { param($m); Write-Host "  [WARN]  $m" -ForegroundColor Yellow }
function Step  { param($m); Write-Host "`n  >> $m" -ForegroundColor Magenta }

# ---- Banner --------------------------------------------------
Clear-Host
Write-Host ""
Write-Host "  +====================================================+" -ForegroundColor Cyan
Write-Host "  |   Vulntrix  --  Local Pentest AI                  |" -ForegroundColor Cyan
Write-Host "  |   Windows One-Click Installer                     |" -ForegroundColor Cyan
Write-Host "  +====================================================+" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Info "Install directory: $ScriptDir"

# ---- Admin check ---------------------------------------------
Step "Checking privileges"
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) {
    Warn "Not running as Administrator -- some steps may fail."
    Warn "Re-run from an elevated PowerShell for best results."
}

# ---- Python 3.10+ --------------------------------------------
Step "Checking Python 3.10+"

$PyCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) {
                $PyCmd = $cmd
                break
            }
        }
    }
    catch { }
}

if (-not $PyCmd) {
    Warn "Python 3.10+ not found. Opening download page..."
    Start-Process "https://www.python.org/downloads/"
    Write-Host ""
    Write-Host "  1. Download and install Python 3.11+" -ForegroundColor Yellow
    Write-Host "  2. CHECK 'Add Python to PATH' during install" -ForegroundColor Yellow
    Write-Host "  3. Re-run this script" -ForegroundColor Yellow
    Read-Host "`n  Press Enter after installing Python..."

    foreach ($cmd in @("python", "python3", "py")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "Python 3\.(\d+)") {
                $minor = [int]$Matches[1]
                if ($minor -ge 10) {
                    $PyCmd = $cmd
                    break
                }
            }
        }
        catch { }
    }
    if (-not $PyCmd) {
        Write-Host "  [ERR]  Python 3.10+ still not found. Install it and re-run." -ForegroundColor Red
        exit 1
    }
}

$PyVersion = & $PyCmd --version 2>&1
Ok "Python: $PyVersion"

# ---- Virtual environment -------------------------------------
Step "Setting up Python virtual environment"

$VenvDir = Join-Path $ScriptDir ".venv"
if (-not (Test-Path $VenvDir)) {
    & $PyCmd -m venv $VenvDir
    Ok "Virtual environment created"
}
else {
    Ok "Virtual environment already exists"
}

$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
$PyExe  = Join-Path $VenvDir "Scripts\python.exe"

& $PyExe -m pip install --upgrade pip --quiet

# ---- Python packages -----------------------------------------
Step "Installing Python packages"

$Packages = @(
    "fastapi",
    "uvicorn[standard]",
    "httpx",
    "python-multipart",
    "rich",
    "prompt_toolkit",
    "pydantic",
    "pystray",
    "Pillow"
)

foreach ($pkg in $Packages) {
    Info "Installing $pkg..."
    & $PipExe install $pkg --quiet
}

$ReqFile = Join-Path $ScriptDir "requirements.txt"
if (Test-Path $ReqFile) {
    & $PipExe install -r $ReqFile --quiet
}
Ok "Python packages installed"

# ---- Ollama --------------------------------------------------
Step "Installing Ollama"

$OllamaExists = Get-Command ollama -ErrorAction SilentlyContinue
if ($OllamaExists) {
    $OllamaVer = (& ollama --version 2>&1) | Select-Object -First 1
    Ok "Ollama already installed: $OllamaVer"
}
else {
    Info "Downloading Ollama for Windows..."
    $OllamaInstaller = Join-Path $env:TEMP "OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $OllamaInstaller -UseBasicParsing
    Info "Running Ollama installer..."
    Start-Process $OllamaInstaller -Wait
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")
    Ok "Ollama installed"
}

# ---- Pull models ---------------------------------------------
Step "Pulling AI models"

Write-Host ""
Write-Host "  Which models do you want to pull?" -ForegroundColor Yellow
Write-Host "  1)  mistral         ~4GB  -- pentest reasoning"
Write-Host "  2)  deepseek-coder  ~2GB  -- code and exploit generation"
Write-Host "  3)  dolphin-mistral ~4GB  -- UNCENSORED general chat"
Write-Host "  4)  All of the above (recommended)"
Write-Host "  5)  Skip -- pull later via the app"
Write-Host ""
$Choice = Read-Host "  Choice [4]"
if ([string]::IsNullOrWhiteSpace($Choice)) { $Choice = "4" }

$OllamaRunning = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if (-not $OllamaRunning) {
    Info "Starting Ollama service..."
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

function Pull-Model {
    param([string]$Model)
    Info "Pulling $Model..."
    try {
        & ollama pull $Model
        Ok "$Model ready"
    }
    catch {
        Warn "Failed to pull $Model -- you can pull it later inside the app"
    }
}

switch ($Choice) {
    "1" { Pull-Model "mistral" }
    "2" { Pull-Model "deepseek-coder" }
    "3" { Pull-Model "dolphin-mistral" }
    "4" {
        Pull-Model "mistral"
        Pull-Model "deepseek-coder"
        Pull-Model "dolphin-mistral"
    }
    default { Info "Skipping model pull." }
}

# ---- start.bat -----------------------------------------------
Step "Creating launch script"

$StartBat = Join-Path $ScriptDir "start.bat"
$StartBatContent = @"
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
"@
$StartBatContent | Set-Content -Path $StartBat -Encoding ASCII
Ok "start.bat created"

# ---- Desktop shortcut ----------------------------------------
Step "Creating desktop shortcut"

$WshShell = New-Object -ComObject WScript.Shell

$DesktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "AI UnCensored Bot.lnk"
$Shortcut = $WshShell.CreateShortcut($DesktopLnk)
$Shortcut.TargetPath       = $PyExe
$Shortcut.Arguments        = "`"$(Join-Path $ScriptDir 'desktop_app.py')`""
$Shortcut.WorkingDirectory = $ScriptDir
$Shortcut.Description      = "AI-Powered Pentest + Uncensored Chat"
$Shortcut.WindowStyle       = 1

$IcoPath = Join-Path $ScriptDir "web_ui\favicon.ico"
if (Test-Path $IcoPath) {
    $Shortcut.IconLocation = $IcoPath
}
$Shortcut.Save()
Ok "Desktop shortcut created: AI UnCensored Bot"

# ---- Start Menu entry ----------------------------------------
$StartMenuDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\AI-UnCensored-Bot"
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

$StartMenuLnk = $WshShell.CreateShortcut("$StartMenuDir\AI UnCensored Bot.lnk")
$StartMenuLnk.TargetPath       = $PyExe
$StartMenuLnk.Arguments        = "`"$(Join-Path $ScriptDir 'desktop_app.py')`""
$StartMenuLnk.WorkingDirectory = $ScriptDir
$StartMenuLnk.Description      = "AI-Powered Pentest + Uncensored Chat"
if (Test-Path $IcoPath) { $StartMenuLnk.IconLocation = $IcoPath }
$StartMenuLnk.Save()
Ok "Start Menu entry created"

# ---- Auto-start option ---------------------------------------
Step "Windows startup (optional)"

$AutoStart = Read-Host "  Start with Windows login? [y/N]"
if ($AutoStart -match "^[Yy]") {
    $RegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $AppArg  = "`"$PyExe`" `"$(Join-Path $ScriptDir 'desktop_app.py')`""
    Set-ItemProperty -Path $RegPath -Name "AI-UnCensored-Bot" -Value $AppArg
    Ok "Added to Windows startup"
}
else {
    Info "Skipping auto-start"
}

# ---- Done ----------------------------------------------------
Write-Host ""
Write-Host "  +==========================================+" -ForegroundColor Green
Write-Host "    OK  Installation complete!" -ForegroundColor Green
Write-Host "  +==========================================+" -ForegroundColor Green
Write-Host ""
Write-Host "  Launch options:" -ForegroundColor White
Write-Host "  1.  Desktop shortcut: 'AI UnCensored Bot'" -ForegroundColor Cyan
Write-Host "  2.  Start Menu: AI-UnCensored-Bot" -ForegroundColor Cyan
Write-Host "  3.  Double-click: start.bat" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Web UI: http://localhost:8000" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Pull more uncensored models:" -ForegroundColor White
Write-Host "    ollama pull dolphin-mistral" -ForegroundColor Yellow
Write-Host "    ollama pull nous-hermes2" -ForegroundColor Yellow
Write-Host ""

$LaunchNow = Read-Host "  Launch the app now? [Y/n]"
if ($LaunchNow -notmatch "^[Nn]") {
    Start-Process $PyExe -ArgumentList "`"$(Join-Path $ScriptDir 'desktop_app.py')`"" -WorkingDirectory $ScriptDir
}
