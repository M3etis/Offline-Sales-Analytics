# Desktop dev mode launcher (Windows)
# Starts frontend dev server, backend, and Electron

$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent
$Root = Split-Path -Parent $Dir

$Green = "`e[32m"
$Blue = "`e[34m"
$NC = "`e[0m"

# Cleanup
$cleanup = {
    Write-Host "`n${Blue}[dev] Stopping services...${NC}"
    Get-Job | Stop-Job -PassThru | Remove-Job -ErrorAction SilentlyContinue
    Write-Host "${Green}[dev] Stopped.${NC}"
}
Register-EngineEvent PowerShell.Exiting -Action $cleanup | Out-Null

# Free ports
foreach ($port in @(5173, 8000)) {
    $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Write-Host "${Blue}[dev] Freeing port $port...${NC}"
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
    }
}

# Install desktop deps
if (-not (Test-Path "$Dir\node_modules")) {
    Write-Host "${Blue}[dev] Installing desktop dependencies...${NC}"
    Push-Location $Dir
    npm install --silent
    Pop-Location
}

# Frontend dev server
Write-Host "${Blue}[dev] Starting frontend dev server (port 5173)...${NC}"
$frontendJob = Start-Job -ScriptBlock {
    Set-Location "$using:Root\frontend"
    npm run dev
}

# Backend
Write-Host "${Blue}[dev] Starting backend (port 8000)...${NC}"
$venvPython = "$Root\venv\Scripts\python.exe"
$backendJob = Start-Job -ScriptBlock {
    Set-Location $using:Root
    & $using:venvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
}

# Wait for backend
Write-Host "${Blue}[dev] Waiting for backend...${NC}"
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
        $ready = $true
        Write-Host "${Green}[dev] Backend ready.${NC}"
        break
    } catch { Start-Sleep -Seconds 1 }
}

# Wait for frontend
Write-Host "${Blue}[dev] Waiting for frontend...${NC}"
for ($i = 0; $i -lt 15; $i++) {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 1
        Write-Host "${Green}[dev] Frontend ready.${NC}"
        break
    } catch { Start-Sleep -Seconds 1 }
}

# Electron
Write-Host "${Green}[dev] Starting Electron...${NC}"
Push-Location $Dir
& "$Dir\node_modules\.bin\electron.cmd" dist-electron\main\index.js
Pop-Location

# Cleanup
& $cleanup
