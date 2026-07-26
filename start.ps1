# Sales Analytics — Windows PowerShell launcher
# Usage: right-click → Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File start.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$Green = "`e[32m"
$Blue = "`e[34m"
$Red = "`e[31m"
$Yellow = "`e[33m"
$NC = "`e[0m"

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
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

# Download model
$envContent = Get-Content .env -ErrorAction SilentlyContinue
$ollamaModel = "qwen2.5:14b"
if ($envContent) {
    $modelLine = $envContent | Where-Object { $_ -match "^OLLAMA_MODEL=" }
    if ($modelLine) { $ollamaModel = ($modelLine -split "=",2)[1].Trim() }
}

$modelList = ollama list 2>$null
if ($modelList -notmatch [regex]::Escape($ollamaModel)) {
    Write-Host "${Yellow}Downloading $ollamaModel (~9 GB)...${NC}"
    ollama pull $ollamaModel
}
Write-Host "${Green}Model $ollamaModel ready.${NC}"

# ─────────────────────────────────────────────
# 2. Python venv
# ─────────────────────────────────────────────
Write-Host "`n${Green}[2/5] Python dependencies...${NC}"

$python = "python"
if (Get-Command python3 -ErrorAction SilentlyContinue) { $python = "python3" }

if (-not (Test-Path "venv")) {
    Write-Host "${Blue}Creating venv...${NC}"
    & $python -m venv venv
}

$venvPython = ".\venv\Scripts\python.exe"
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
        $key = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
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
        # Generate SECRET_KEY if empty
        $envContent = Get-Content .env -Raw
        if ($envContent -match 'SECRET_KEY=$' -or $envContent -match 'SECRET_KEY=""') {
            $key = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
            (Get-Content .env) -replace 'SECRET_KEY=.*', "SECRET_KEY=$key" | Set-Content .env
            Write-Host "${Green}SECRET_KEY generated.${NC}"
        }
    }
}
Write-Host "${Green}Config ready.${NC}"

# ─────────────────────────────────────────────
# 5. Start services
# ─────────────────────────────────────────────
Write-Host "`n${Green}[5/5] Starting services...${NC}"

# Kill existing processes on ports
foreach ($port in @(8000, 5173)) {
    $proc = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($proc) { Stop-Process -Id $proc.OwningProcess -Force -ErrorAction SilentlyContinue }
}

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

Write-Host "${Blue}  Starting backend (port 8000)...${NC}"
$backendJob = Start-Job -ScriptBlock {
    Set-Location $using:ScriptDir
    & "$using:ScriptDir\venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 *> "$using:ScriptDir\logs\backend.log"
}

# Wait for backend
$ready = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
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
Write-Host "${Blue}  Starting frontend (port 5173)...${NC}"
$frontendJob = Start-Job -ScriptBlock {
    Set-Location "$using:ScriptDir\frontend"
    npm run dev *> "$using:ScriptDir\logs\frontend.log"
}

Start-Sleep -Seconds 3

# Check frontend
$frontendReady = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 1 -UseBasicParsing
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
Start-Process "http://localhost:5173"

Write-Host ""
Write-Host "${Green}======================================================${NC}"
Write-Host "${Green}  All services running!${NC}"
Write-Host "${Green}======================================================${NC}"
Write-Host "  Frontend:  ${Blue}http://localhost:5173${NC}"
Write-Host "  Backend:   ${Blue}http://localhost:8000${NC}"
Write-Host "  Stop:      ${Yellow}Ctrl+C${NC}"
Write-Host "${Green}======================================================${NC}`n"

# Wait
try { Wait-Job -Job $backendJob, $frontendJob } finally { & $cleanup }
