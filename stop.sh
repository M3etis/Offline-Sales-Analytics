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

# ─────────────────────────────────────────────
# 1. FastAPI / Uvicorn (порт 8000)
# ─────────────────────────────────────────────
pids=$(lsof -ti :8000 2>/dev/null)
if [ -n "$pids" ]; then
    echo -e "${YELLOW}[1/3] Остановка FastAPI backend (порт 8000)...${NC}"
    echo "$pids" | xargs kill 2>/dev/null
    sleep 2
    # Принудительное завершение, если процесс не остановился
    pids=$(lsof -ti :8000 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null
    fi
    echo -e "${GREEN}  Backend остановлен.${NC}"
    stopped=$((stopped + 1))
else
    echo -e "${GREEN}[1/3] FastAPI backend не запущен.${NC}"
fi

# ─────────────────────────────────────────────
# 2. Vite frontend (порт 5173)
# ─────────────────────────────────────────────
pids=$(lsof -ti :5173 2>/dev/null)
if [ -n "$pids" ]; then
    echo -e "${YELLOW}[2/3] Остановка Vite frontend (порт 5173)...${NC}"
    echo "$pids" | xargs kill 2>/dev/null
    sleep 2
    pids=$(lsof -ti :5173 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null
    fi
    echo -e "${GREEN}  Frontend остановлен.${NC}"
    stopped=$((stopped + 1))
else
    echo -e "${GREEN}[2/3] Vite frontend не запущен.${NC}"
fi

# ─────────────────────────────────────────────
# 3. Ollama (порт 11434)
# ─────────────────────────────────────────────
pids=$(lsof -ti :11434 2>/dev/null)
if [ -n "$pids" ]; then
    echo -e "${YELLOW}[3/3] Остановка Ollama (порт 11434)...${NC}"
    echo "$pids" | xargs kill 2>/dev/null
    sleep 2
    pids=$(lsof -ti :11434 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null
    fi
    echo -e "${GREEN}  Ollama остановлен.${NC}"
    stopped=$((stopped + 1))
else
    echo -e "${GREEN}[3/3] Ollama не запущен.${NC}"
fi

# ─────────────────────────────────────────────
# Дополнительно: убить процессы по имени
# ─────────────────────────────────────────────
# uvicorn app.main
pkill -f "uvicorn app.main" 2>/dev/null
# vite dev server
pkill -f "vite" 2>/dev/null

echo ""
if [ $stopped -eq 0 ]; then
    echo -e "${GREEN}Все сервисы уже были остановлены.${NC}"
else
    echo -e "${GREEN}Остановлено сервисов: $stopped${NC}"
fi

echo -e "${BLUE}======================================================${NC}"
echo -e "${GREEN}  Готово.${NC}"
echo -e "${BLUE}======================================================${NC}"
