# Sales Analytics — Windows PowerShell launcher
# Usage: right-click → Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File start.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ESC via [char]27: the `e escape exists only in PowerShell 6+, Windows PowerShell 5.1 printed "e[32m"
$ESC = [char]27
$Green = "$ESC[32m"
$Blue = "$ESC[34m"
$Red = "$ESC[31m"
$Yellow = "$ESC[33m"
$NC = "$ESC[0m"

Write-Host "${Blue}======================================================${NC}"
Write-Host "${Blue}  Sales Analytics — Windows${NC}"
Write-Host "${Blue}======================================================${NC}`n"

# Cleanup on exit
$cleanup = {
    Write-Host "`n${Blue}Stopping services...${NC}"
    Get-Job | Stop-Job -PassThru | Remove-Job
    Write-Host "${Green}Stopped.${NC}"
}
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

# ─────────────────────────────────────────────
# 1. Ollama
# ─────────────────────────────────────────────
Write-Host "${Green}[1/5] Checking Ollama...${NC}"

$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaPath) {
    Write-Host "${Yellow}Ollama not found. Installing...${NC}"
    # Download Ollama installer for Windows
    $installer = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
    Start-Process -FilePath $installer -Wait
    # Refresh PATH
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    Write-Host "${Green}Ollama installed.${NC}"
}

# Start Ollama service
try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
    Write-Host "${Green}Ollama running.${NC}"
} catch {
    Write-Host "${Blue}Starting Ollama...${NC}"
    $ollamaProc = Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden -PassThru
    # Remember that this script started it: stop.ps1 stops only this Ollama, not whatever listens
    # on 11434 (the Ollama app, a service or `ollama serve` the user started)
    New-Item -ItemType Directory -Force -Path "logs" | Out-Null
    Set-Content -Path "logs\ollama.pid" -Value $ollamaProc.Id
    Start-Sleep -Seconds 5
}

# ─────────────────────────────────────────────
# 2. Python venv
# ─────────────────────────────────────────────
Write-Host "`n${Green}[2/5] Python dependencies...${NC}"

# Supported versions match start.sh: pandas 2.x segfaults on Python 3.14+
$pyCheck = "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)"
function Test-SupportedPython([string]$exe, [string[]]$extra = @()) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    # Windows PowerShell 5.1 при "Stop" превращает stderr нативной команды (даже с 2>$null)
    # в ошибку и обрывает скрипт на первом неподходящем Python — проверяем без этого
    $ErrorActionPreference = "Continue"
    try { & $exe @extra -c $pyCheck 2>$null | Out-Null } catch { return $false }
    return ($LASTEXITCODE -eq 0)
}

$venvPython = ".\venv\Scripts\python.exe"
$venvOk = (Test-Path $venvPython) -and (Test-SupportedPython $venvPython)

if (-not $venvOk) {
    # The py launcher can pick an exact version; then python3/python from PATH
    $python = $null
    $pythonArgs = @()
    foreach ($v in @("-3.12", "-3.13", "-3.11", "-3.10")) {
        if (Test-SupportedPython "py" @($v)) { $python = "py"; $pythonArgs = @($v); break }
    }
    if (-not $python) {
        foreach ($cmd in @("python3", "python")) {
            if (Test-SupportedPython $cmd) { $python = $cmd; break }
        }
    }
    if (-not $python) {
        Write-Host "${Red}Python 3.10-3.13 not found. Install Python 3.12 from python.org and run again.${NC}"
        exit 1
    }
    if (Test-Path "venv") {
        Write-Host "${Yellow}venv uses an unsupported Python version, recreating...${NC}"
        Remove-Item -Recurse -Force "venv"
    }
    Write-Host "${Blue}Creating venv...${NC}"
    & $python @pythonArgs -m venv venv
}

$venvPip = ".\venv\Scripts\pip.exe"

$needInstall = $false
if (-not (Test-Path "venv\.deps_installed")) { $needInstall = $true }
elseif ((Get-Item requirements.txt).LastWriteTime -gt (Get-Item "venv\.deps_installed").LastWriteTime) { $needInstall = $true }

if ($needInstall) {
    Write-Host "${Blue}Installing Python packages...${NC}"
    & $venvPip install -q -r requirements.txt
    New-Item -Path "venv\.deps_installed" -ItemType File -Force | Out-Null
}
Write-Host "${Green}Python ready.${NC}"

# Download model — after venv: the model chosen in the UI wins over OLLAMA_MODEL from .env
# (same rule as the backend; see scripts/startup_model.py)
$ollamaModel = & { $ErrorActionPreference = "Continue"; & .\venv\Scripts\python.exe scripts\startup_model.py 2>$null }
if (-not $ollamaModel) { $ollamaModel = "qwen2.5:14b" }
$ollamaModel = "$ollamaModel".Trim()

$modelList = & { $ErrorActionPreference = "Continue"; ollama list 2>$null }
if ($modelList -notmatch [regex]::Escape($ollamaModel)) {
    Write-Host "${Yellow}Downloading $ollamaModel (~9 GB)...${NC}"
    ollama pull $ollamaModel
}
Write-Host "${Green}Model $ollamaModel ready.${NC}"

# ─────────────────────────────────────────────
# 3. Frontend
# ─────────────────────────────────────────────
Write-Host "`n${Green}[3/5] Frontend dependencies...${NC}"

$needNpm = $false
if (-not (Test-Path "frontend\node_modules")) { $needNpm = $true }
elseif ((Get-Item "frontend\package.json").LastWriteTime -gt (Get-Item "frontend\node_modules\.package-lock.json" -ErrorAction SilentlyContinue).LastWriteTime) { $needNpm = $true }

if ($needNpm) {
    Write-Host "${Blue}npm install...${NC}"
    Push-Location frontend
    npm install --silent
    Pop-Location
}
Write-Host "${Green}Frontend ready.${NC}"

# Cryptographically secure 64-hex-char key (Get-Random is not a CSPRNG)
function New-SecretKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

# ─────────────────────────────────────────────
# 4. Config
# ─────────────────────────────────────────────
Write-Host "`n${Green}[4/5] Configuration...${NC}"

if (-not (Test-Path ".env")) {
    Write-Host "${Blue}Creating .env...${NC}"
    if (Test-Path ".env.example") {
        Copy-Item .env.example .env
    }
    if (-not (Test-Path ".env")) {
        Write-Host "${Yellow}.env.example not found, creating .env with defaults...${NC}"
        $key = New-SecretKey
        @"
SECRET_KEY=$key
ALLOWED_ORIGINS=*
DB_PATH=data/processed/sales.duckdb
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:14b
WHISPER_MODEL=bzikst/faster-whisper-large-v3-russian
WHISPER_LANGUAGE=ru
PIPER_MODEL=ru_RU-irina-medium
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
LOG_DIR=logs
DATA_DIR=data
DEMO_FILE=data/demo/demo_sales.csv
MAX_RESULT_ROWS=1000
MAX_UPLOAD_SIZE_MB=50
"@ | Set-Content .env
        Write-Host "${Green}SECRET_KEY generated.${NC}"
    } else {
    }
}
# Generate SECRET_KEY if empty or still the .env.example placeholder (it is long enough to pass the
# backend check, but identical on every install). Also for an existing .env, not only a fresh one
if (Test-Path ".env") {
    $envContent = Get-Content .env -Raw
    if ($envContent -match '(?m)^SECRET_KEY=(""|your-secret-key\S*)?\s*$') {
        $key = New-SecretKey
        (Get-Content .env) -replace '^SECRET_KEY=.*', "SECRET_KEY=$key" | Set-Content .env
        Write-Host "${Green}SECRET_KEY generated.${NC}"
    }
}
Write-Host "${Green}Config ready.${NC}"

# ─────────────────────────────────────────────
# 5. Start services
# ─────────────────────────────────────────────
Write-Host "`n${Green}[5/5] Starting services...${NC}"

# Ports: BACKEND_PORT / FRONTEND_PORT from the environment, 8000 / 5173 by default (same as start.sh)
$BackendPort = if ($env:BACKEND_PORT) { $env:BACKEND_PORT } else { "8000" }
$FrontendPort = if ($env:FRONTEND_PORT) { $env:FRONTEND_PORT } else { "5173" }
foreach ($p in @(@("BACKEND_PORT", $BackendPort), @("FRONTEND_PORT", $FrontendPort))) {
    if ($p[1] -notmatch '^\d+$' -or [int]$p[1] -lt 1 -or [int]$p[1] -gt 65535) {
        Write-Host "${Red}$($p[0]) must be a port number, got: '$($p[1])'${NC}"
        exit 1
    }
}
$BackendPort = [int]$BackendPort
$FrontendPort = [int]$FrontendPort

# PIDs listening on the port. Only Listen: otherwise the first entry can be a client
# (e.g. the browser with the app open) or a TIME_WAIT socket with PID 0
function Get-ListenPids([int]$port) {
    @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 })
}

function Test-PortBusy([int]$port) {
    if ((Get-ListenPids $port).Count -gt 0) { return $true }
    # Also try to bind: catches listeners we cannot see and ports reserved by Hyper-V/WSL
    # (netsh int ipv4 show excludedportrange), where nothing listens but binding fails
    try {
        $l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        $l.Start(); $l.Stop()
    } catch { return $true }
    # And on ::1: the browser opens localhost, which may resolve to ::1 - the port must be free there too.
    # No IPv6 on the machine - nothing can listen there either
    try {
        $l6 = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::IPv6Loopback, $port)
        $l6.Start(); $l6.Stop()
    } catch [System.Net.Sockets.SocketException] {
        if ($_.Exception.SocketErrorCode -eq [System.Net.Sockets.SocketError]::AddressAlreadyInUse -or
            $_.Exception.SocketErrorCode -eq [System.Net.Sockets.SocketError]::AccessDenied) { return $true }
    } catch { }
    return $false
}

# Normalizes paths in a command line: / -> \, repeated \ collapsed. npm shims start Vite as
# node "...\node_modules\.bin\\..\vite\bin\vite.js" (cmd) or ...\node_modules\.bin/../vite/bin/vite.js (ps1)
function Get-NormPath([string]$s) { $s -replace '[\\/]+', '\' }

$ProjectRoot = Get-NormPath ($ScriptDir.TrimEnd('\', '/') + '\')
# The Vite binary itself: .bin\vite or vite\bin\vite.js in any path form (including .bin\\..\vite\bin\vite.js
# from the npm shim), followed by a quote, space or end of line: not vitest and not the vite.cmd wrapper
$VitePattern = 'node_modules[\\/]+\.bin[\\/]+vite(?=["''\s]|$)|vite[\\/]+bin[\\/]+vite\.js(?=["''\s]|$)'

# Ours = backend or Vite started from this project. The name alone is not enough:
# `uvicorn app.main:app` is common in other projects. Windows does not expose a process's
# working directory, so we look in the exe path or command line for exactly the service folder:
# this project's venv\ for the backend (also in the parent: venv\Scripts\python.exe starts the base
# interpreter as a child process), frontend\node_modules\ for Vite. Not any path inside the project:
# a git worktree in .claude\worktrees\<name>\ is another instance of the app
function Test-OwnProcess([int]$procId) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    $cmd = "$($proc.CommandLine)"
    $chain = @($proc)
    if ($cmd -match 'uvicorn\s+app\.main:app') {
        $dir = "${ProjectRoot}venv\"
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent) { $chain += $parent }
    } elseif ($cmd -match $VitePattern) {
        $dir = "${ProjectRoot}frontend\node_modules\"
    } else {
        return $false
    }
    foreach ($p in $chain) {
        foreach ($s in @("$($p.ExecutablePath)", "$($p.CommandLine)")) {
            if ((Get-NormPath $s).IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        }
    }
    return $false
}

# Stops a previous run of this project (backend or Vite from this folder) on ANY port.
# The requested port alone is not enough: a previous run may have moved to a fallback port
# (8001, 5174), and a backend left running keeps the DuckDB file locked, so the new one fails
# on startup. Other apps are left alone.
function Stop-PreviousRun {
    $own = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -gt 0 -and (Test-OwnProcess $_) })
    if ($own.Count -eq 0) { return }
    Write-Host "${Blue}  Stopping the previous run of this project (PID: $($own -join ', '))...${NC}"
    # Windows has no SIGTERM for console processes, so Stop-Process -Force as before. Then wait
    # for the processes to actually exit: their ports and the DuckDB lock are released only then
    foreach ($p in $own) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    Wait-Process -Id $own -Timeout 10 -ErrorAction SilentlyContinue
}

# Picks a port for the service. Other apps are left alone: Stop-Process -Force on the port used
# to kill e.g. Docker Desktop, which forwards container ports — instead the nearest free port is used.
# $reserved: a port already given to another service that is not listening yet (backend starts later).
function Select-ServicePort([int]$port, [string]$name, [int]$reserved = 0) {
    $candidate = $port
    $max = [Math]::Min($port + 50, 65535)
    while ($candidate -eq $reserved -or (Test-PortBusy $candidate)) {
        $candidate++
        if ($candidate -gt $max) {
            Write-Host "${Red}ERROR: ${name}: no free port in range $port-$max${NC}"
            exit 1
        }
    }
    if ($candidate -ne $port) {
        if ($port -eq $reserved) {
            Write-Host "${Yellow}  Port $port is already taken by another service - $name will use port $candidate${NC}"
        } else {
            $who = (Get-ListenPids $port | ForEach-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName } |
                Where-Object { $_ } | Sort-Object -Unique) -join ", "
            $whoText = if ($who) { " ($who)" } else { "" }
            Write-Host "${Yellow}  Port $port is used by another app$whoText - $name will use port $candidate${NC}"
        }
    }
    return $candidate
}

Stop-PreviousRun
$BackendPort = Select-ServicePort $BackendPort "backend"
$FrontendPort = Select-ServicePort $FrontendPort "frontend" $BackendPort
$BackendUrl = "http://127.0.0.1:$BackendPort"
$FrontendUrl = "http://127.0.0.1:$FrontendPort"
# Browser address is localhost, as before port selection: login, the selected dataset and settings are
# stored by the browser per page address, and with 127.0.0.1 they would have to be set again.
# Safe: the port is free on both 127.0.0.1 and ::1 (Test-PortBusy) - if the browser tries ::1 first,
# nothing listens there and it falls back to 127.0.0.1, where Vite listens
$OpenUrl = "http://localhost:$FrontendPort"

# Backend
$venvPython = ".\venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "${Red}venv is corrupted or not found.${NC}"
    $answer = Read-Host "Delete venv and recreate? (y/N)"
    if ($answer -eq "y" -or $answer -eq "Y") {
        Remove-Item -Recurse -Force venv
        Write-Host "${Blue}Restarting script...${NC}"
        & $MyInvocation.MyCommand.Path
        exit
    } else {
        Write-Host "${Red}Installation cancelled.${NC}"
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path "logs" | Out-Null

Write-Host "${Blue}  Starting backend (port $BackendPort)...${NC}"
$backendJob = Start-Job -ScriptBlock {
    Set-Location $using:ScriptDir
    & "$using:ScriptDir\venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $using:BackendPort *> "$using:ScriptDir\logs\backend.log"
}

# Wait for backend
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $null = Invoke-RestMethod -Uri "$BackendUrl/health" -TimeoutSec 1
        $ready = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}

if (-not $ready) {
    Write-Host "${Red}ERROR: Backend failed to start within 15 seconds!${NC}"
    Write-Host "${Yellow}Logs: logs\backend.log${NC}"
    if (Test-Path "logs\backend.log") { Get-Content "logs\backend.log" -Tail 20 } else { Write-Host "(log empty)" }
    exit 1
}

# Frontend
Write-Host "${Blue}  Starting frontend (port $FrontendPort)...${NC}"
# BACKEND_PORT is read by vite.config.ts (proxy for /api/v1 and /ws). --strictPort: without it
# Vite silently moves to the next port and the check below would hit another app.
# npx vite instead of `npm run dev -- ...`: the npm.ps1 shim can swallow `--` in PowerShell
$frontendJob = Start-Job -ScriptBlock {
    Set-Location "$using:ScriptDir\frontend"
    $env:BACKEND_PORT = "$using:BackendPort"
    npx vite --port $using:FrontendPort --strictPort *> "$using:ScriptDir\logs\frontend.log"
}

Start-Sleep -Seconds 3

# Check frontend
$frontendReady = $false
for ($i = 0; $i -lt 10; $i++) {
    # Vite already exited (e.g. --strictPort found the port busy): no point waiting
    if ($frontendJob.State -ne 'Running') { break }
    try {
        $null = Invoke-WebRequest -Uri $FrontendUrl -TimeoutSec 1 -UseBasicParsing
        $frontendReady = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}

if (-not $frontendReady) {
    Write-Host "${Red}ERROR: Frontend failed to start!${NC}"
    Write-Host "${Yellow}Logs: logs\frontend.log${NC}"
    if (Test-Path "logs\frontend.log") { Get-Content "logs\frontend.log" -Tail 20 } else { Write-Host "(log empty)" }
    exit 1
}

# Open browser
Write-Host "${Blue}  Opening browser...${NC}"
Start-Process $OpenUrl

Write-Host ""
Write-Host "${Green}======================================================${NC}"
Write-Host "${Green}  All services running!${NC}"
Write-Host "${Green}======================================================${NC}"
Write-Host "  Frontend:  ${Blue}$OpenUrl${NC} (or $FrontendUrl)"
Write-Host "  Backend:   ${Blue}$BackendUrl${NC}"
Write-Host "  Stop:      ${Yellow}Ctrl+C${NC}"
Write-Host "${Green}======================================================${NC}`n"

# Wait
try { Wait-Job -Job $backendJob, $frontendJob } finally { & $cleanup }
