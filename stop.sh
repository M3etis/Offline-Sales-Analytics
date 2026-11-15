#!/bin/bash

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}  Sales Analytics — Остановка всех сервисов${NC}"
echo -e "${BLUE}======================================================${NC}\n"

stopped=0
# Физический путь без симлинков — с ним сравнивается cwd процессов (/proc/PID/cwd и lsof отдают его)
PROJECT_DIR_REAL="$(cd "$(dirname "$0")" && pwd -P)"
# PID Ollama, которую запустил start.sh. Чужую Ollama (приложение, служба, `ollama serve` вручную) не трогаем
OLLAMA_PID_FILE="$PROJECT_DIR_REAL/logs/ollama.pid"

# Рабочая папка процесса. На Linux — /proc/PID/cwd: lsof там часто не установлен, и без него
# ни один процесс не признавался своим. На macOS /proc нет — спрашиваем lsof
proc_cwd() {
    local cwd=""
    [ -d /proc ] && cwd=$(readlink "/proc/$1/cwd" 2>/dev/null)
    if [ -z "$cwd" ] && command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    fi
    echo "$cwd"
}

# Бэкенд и Vite ищем как процессы ЭТОГО проекта (как в start.sh), а не по портам 8000/5173:
# start.sh уходит на соседний порт, если 8000/5173 заняты другим приложением (Docker, OrbStack),
# и kill по порту убивал бы это приложение, а наш сервис на 8001 оставался бы работать.
# PID процессов, чья командная строка подходит под регулярное выражение pgrep -f, а рабочая папка —
# ровно $2. Не «любая внутри проекта»: git worktree в .claude/worktrees/<имя>/ — другой экземпляр
# приложения, его не трогаем
own_pids() {
    local pid
    for pid in $(pgrep -f "$1" 2>/dev/null); do
        [ "$(proc_cwd "$pid")" = "$2" ] && echo "$pid"
    done
}

# Мягкая остановка: ждём завершения до 10 секунд, оставшихся добиваем
stop_pids() {
    local pid alive="" i
    kill $1 2>/dev/null
    for i in {1..20}; do
        alive=""
        for pid in $1; do
            kill -0 "$pid" 2>/dev/null && alive="$alive $pid"
        done
        [ -z "$alive" ] && return 0
        sleep 0.5
    done
    kill -9 $alive 2>/dev/null
}

# ─────────────────────────────────────────────
# 1. FastAPI / Uvicorn
# ─────────────────────────────────────────────
pids=$(own_pids "uvicorn app\.main:app" "$PROJECT_DIR_REAL")
if [ -n "$pids" ]; then
    echo -e "${YELLOW}[1/3] Остановка FastAPI backend...${NC}"
    stop_pids "$pids"
    echo -e "${GREEN}  Backend остановлен.${NC}"
    stopped=$((stopped + 1))
else
    echo -e "${GREEN}[1/3] FastAPI backend не запущен.${NC}"
fi

# ─────────────────────────────────────────────
# 2. Vite frontend
# ─────────────────────────────────────────────
# Сам бинарник Vite (.bin/vite или vite/bin/vite.js), за ним пробел или конец строки: не vitest
pids=$(own_pids "node_modules/\.bin/vite( |$)|vite/bin/vite\.js( |$)" "$PROJECT_DIR_REAL/frontend")
if [ -n "$pids" ]; then
    echo -e "${YELLOW}[2/3] Остановка Vite frontend...${NC}"
    stop_pids "$pids"
    echo -e "${GREEN}  Frontend остановлен.${NC}"
    stopped=$((stopped + 1))
else
    echo -e "${GREEN}[2/3] Vite frontend не запущен.${NC}"
fi
# Добивающего `pkill -f "$PROJECT_DIR/frontend/node_modules/.bin/vite"` больше нет: как регулярное
# выражение он совпадал и с .bin/vitest, а свои процессы Vite уже найдены выше по папке проекта

# ─────────────────────────────────────────────
# 3. Ollama — только если её запустил start.sh
# ─────────────────────────────────────────────
ollama_pid=""
if [ -f "$OLLAMA_PID_FILE" ]; then
    ollama_pid=$(tr -cd '0-9' < "$OLLAMA_PID_FILE")
    # PID мог достаться другому процессу после перезагрузки — проверяем, что это всё ещё ollama
    if [ -n "$ollama_pid" ] && ! ps -o command= -p "$ollama_pid" 2>/dev/null | grep -q "ollama"; then
        ollama_pid=""
    fi
fi
if [ -n "$ollama_pid" ]; then
    echo -e "${YELLOW}[3/3] Остановка Ollama (запущена start.sh, PID $ollama_pid)...${NC}"
    stop_pids "$ollama_pid"
    rm -f "$OLLAMA_PID_FILE"
    echo -e "${GREEN}  Ollama остановлена.${NC}"
    stopped=$((stopped + 1))
else
    rm -f "$OLLAMA_PID_FILE"
    if curl -s --max-time 2 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
        echo -e "${GREEN}[3/3] Ollama запущена не этим проектом — оставляю работать.${NC}"
    else
        echo -e "${GREEN}[3/3] Ollama не запущена.${NC}"
    fi
fi

echo ""
if [ $stopped -eq 0 ]; then
    echo -e "${GREEN}Все сервисы уже были остановлены.${NC}"
else
    echo -e "${GREEN}Остановлено сервисов: $stopped${NC}"
fi

echo -e "${BLUE}======================================================${NC}"
echo -e "${GREEN}  Готово.${NC}"
echo -e "${BLUE}======================================================${NC}"
