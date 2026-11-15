# Sales Analytics — Остановка всех сервисов (Windows)
# Usage: right-click → Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File stop.ps1

$ErrorActionPreference = "SilentlyContinue"

# ESC через [char]27: `e есть только в PowerShell 6+, Windows PowerShell 5.1 печатал «e[32m»
$ESC = [char]27
$Green = "$ESC[32m"
$Blue = "$ESC[34m"
$Red = "$ESC[31m"
$Yellow = "$ESC[33m"
$NC = "$ESC[0m"

Write-Host "${Blue}======================================================${NC}"
Write-Host "${Blue}  Sales Analytics — Остановка всех сервисов${NC}"
Write-Host "${Blue}======================================================${NC}`n"

$stopped = 0
# Путь в командной строке — к одному виду: / → \, повторные \ схлопываем. npm-шимы запускают Vite как
# node "...\node_modules\.bin\\..\vite\bin\vite.js" (cmd) или ...\node_modules\.bin/../vite/bin/vite.js (ps1)
function Get-NormPath([string]$s) { $s -replace '[\\/]+', '\' }

$ProjectRoot = Get-NormPath ((Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\', '/') + '\')
# PID Ollama, которую запустил start.ps1. Чужую Ollama (приложение, служба, `ollama serve` вручную) не трогаем
$OllamaPidFile = Join-Path $ProjectRoot "logs\ollama.pid"

# Бэкенд и Vite ищем как процессы ЭТОГО проекта (как в start.ps1), а не по портам 8000/5173:
# start.ps1 уходит на соседний порт, если 8000/5173 заняты другим приложением (Docker Desktop),
# и остановка по порту убивала бы это приложение, а наш сервис на 8001 оставался бы работать.
# Рабочую папку процесса Windows не отдаёт, поэтому ищем в пути exe или командной строке ровно папку
# сервиса: venv\ проекта для бэкенда, frontend\node_modules\ для Vite. Не любой путь внутри проекта:
# git worktree в .claude\worktrees\<имя>\ — другой экземпляр приложения, его не трогаем.
# -WithParent — для бэкенда: venv\Scripts\python.exe запускает базовый интерпретатор дочерним процессом
function Test-InProject($proc, [string]$dir, [switch]$WithParent) {
    $chain = @($proc)
    if ($WithParent) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent) { $chain += $parent }
    }
    foreach ($p in $chain) {
        foreach ($s in @("$($p.ExecutablePath)", "$($p.CommandLine)")) {
            if ((Get-NormPath $s).IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
        }
    }
    return $false
}

$BackendPattern = 'uvicorn\s+app\.main:app'
# Сам бинарник Vite: .bin\vite или vite\bin\vite.js в любом виде пути (в том числе .bin\\..\vite\bin\vite.js
# из npm-шима), за ним кавычка, пробел или конец строки — не vitest и не обёртка vite.cmd
$VitePattern = 'node_modules[\\/]+\.bin[\\/]+vite(?=["''\s]|$)|vite[\\/]+bin[\\/]+vite\.js(?=["''\s]|$)'

# PID процессов проекта, чья командная строка подходит под регулярное выражение
function Get-OwnPids([string]$pattern, [string]$dir, [switch]$WithParent) {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { "$($_.CommandLine)" -match $pattern -and (Test-InProject $_ $dir -WithParent:$WithParent) } |
        Select-Object -ExpandProperty ProcessId)
}

# Сигнала мягкой остановки для консольных процессов в Windows нет — Stop-Process -Force, как и раньше;
# затем ждём, пока процессы действительно завершатся и освободят порт и блокировку DuckDB
function Stop-Pids($ids) {
    foreach ($p in $ids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
    Wait-Process -Id $ids -Timeout 10 -ErrorAction SilentlyContinue
}

# ─────────────────────────────────────────────
# 1. FastAPI / Uvicorn
# ─────────────────────────────────────────────
$ids = Get-OwnPids $BackendPattern "${ProjectRoot}venv\" -WithParent
if ($ids.Count -gt 0) {
    Write-Host "${Yellow}[1/3] Остановка FastAPI backend...${NC}"
    Stop-Pids $ids
    Write-Host "${Green}  Backend остановлен.${NC}"
    $stopped++
} else {
    Write-Host "${Green}[1/3] FastAPI backend не запущен.${NC}"
}

# ─────────────────────────────────────────────
# 2. Vite frontend
# ─────────────────────────────────────────────
$ids = Get-OwnPids $VitePattern "${ProjectRoot}frontend\node_modules\"
if ($ids.Count -gt 0) {
    Write-Host "${Yellow}[2/3] Остановка Vite frontend...${NC}"
    Stop-Pids $ids
    Write-Host "${Green}  Frontend остановлен.${NC}"
    $stopped++
} else {
    Write-Host "${Green}[2/3] Vite frontend не запущен.${NC}"
}

# ─────────────────────────────────────────────
# 3. Ollama — только если её запустил start.ps1
# ─────────────────────────────────────────────
$ollama = $null
if (Test-Path $OllamaPidFile) {
    $ollamaPid = "$(Get-Content $OllamaPidFile -TotalCount 1)".Trim()
    # PID мог достаться другому процессу после перезагрузки — проверяем, что это всё ещё ollama
    if ($ollamaPid -match '^\d+$') {
        $ollama = Get-Process -Id ([int]$ollamaPid) -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'ollama*' }
    }
}
if ($ollama) {
    Write-Host "${Yellow}[3/3] Остановка Ollama (запущена start.ps1, PID $($ollama.Id))...${NC}"
    # Вместе с дочерними процессами: модели обслуживает отдельный процесс-раннер, он держит память
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($ollama.Id)" -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty ProcessId)
    Stop-Pids (@($ollama.Id) + $children)
    Remove-Item $OllamaPidFile -Force -ErrorAction SilentlyContinue
    Write-Host "${Green}  Ollama остановлена.${NC}"
    $stopped++
} else {
    Remove-Item $OllamaPidFile -Force -ErrorAction SilentlyContinue
    $conn = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Write-Host "${Green}[3/3] Ollama запущена не этим проектом — оставляю работать.${NC}"
    } else {
        Write-Host "${Green}[3/3] Ollama не запущена.${NC}"
    }
}

# Убийство по имени (uvicorn*, node с vite) убрано: оно останавливало бэкенды и dev-серверы
# других проектов, а свои процессы уже найдены выше по папке проекта

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
