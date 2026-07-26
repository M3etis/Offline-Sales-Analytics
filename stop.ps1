# Sales Analytics — Остановка всех сервисов (Windows)
# Usage: right-click → Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File stop.ps1

$ErrorActionPreference = "SilentlyContinue"

$Green = "`e[32m"
$Blue = "`e[34m"
$Red = "`e[31m"
$Yellow = "`e[33m"
$NC = "`e[0m"

Write-Host "${Blue}======================================================${NC}"
Write-Host "${Blue}  Sales Analytics — Остановка всех сервисов${NC}"
Write-Host "${Blue}======================================================${NC}`n"

$stopped = 0

# ─────────────────────────────────────────────
# 1. FastAPI / Uvicorn (порт 8000)
# ─────────────────────────────────────────────
$conn = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "${Yellow}[1/3] Остановка FastAPI backend (порт 8000)...${NC}"
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    # Повторная проверка
    $conn = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Write-Host "${Green}  Backend остановлен.${NC}"
    $stopped++
} else {
    Write-Host "${Green}[1/3] FastAPI backend не запущен.${NC}"
}

# ─────────────────────────────────────────────
# 2. Vite frontend (порт 5173)
# ─────────────────────────────────────────────
$conn = Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "${Yellow}[2/3] Остановка Vite frontend (порт 5173)...${NC}"
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $conn = Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Write-Host "${Green}  Frontend остановлен.${NC}"
    $stopped++
} else {
    Write-Host "${Green}[2/3] Vite frontend не запущен.${NC}"
}

# ─────────────────────────────────────────────
# 3. Ollama (порт 11434)
# ─────────────────────────────────────────────
$conn = Get-NetTCPConnection -LocalPort 11434 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) {
    Write-Host "${Yellow}[3/3] Остановка Ollama (порт 11434)...${NC}"
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $conn = Get-NetTCPConnection -LocalPort 11434 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Write-Host "${Green}  Ollama остановлен.${NC}"
    $stopped++
} else {
    Write-Host "${Green}[3/3] Ollama не запущен.${NC}"
}

# ─────────────────────────────────────────────
# Дополнительно: убить процессы по имени
# ─────────────────────────────────────────────
Get-Process -Name "uvicorn*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process -Name "node" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "vite"
} | Stop-Process -Force -ErrorAction SilentlyContinue

# Очистка фоновых задач PowerShell (из start.ps1)
Get-Job | Stop-Job -PassThru -ErrorAction SilentlyContinue | Remove-Job -ErrorAction SilentlyContinue

Write-Host ""
if ($stopped -eq 0) {
    Write-Host "${Green}Все сервисы уже были остановлены.${NC}"
} else {
    Write-Host "${Green}Остановлено сервисов: $stopped${NC}"
}

Write-Host "${Blue}======================================================${NC}"
Write-Host "${Green}  Готово.${NC}"
Write-Host "${Blue}======================================================${NC}"
