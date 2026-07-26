#!/bin/bash

# Цвета
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Автовосстановление повреждённого venv
# Проверяем не только наличие бинарника, но и то, что pip реально работает
# (venv ломается при переносе проекта — shebang в pip указывает на старый путь)
if [ -d "venv" ]; then
    if [ ! -x "./venv/bin/python" ] || ! ./venv/bin/python -m pip --version &>/dev/null; then
        echo -e "${YELLOW}venv повреждён или перенесён, пересоздаю...${NC}"
        rm -rf venv
    fi
fi

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}  Sales Analytics — Полная автоматическая установка${NC}"
echo -e "${BLUE}======================================================${NC}\n"

# Очистка при выходе
cleanup() {
    echo -e "\n${BLUE}Остановка всех сервисов...${NC}"
    jobs -p | xargs kill 2>/dev/null
    echo -e "${GREEN}Приложение остановлено.${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ─────────────────────────────────────────────
# 1. Ollama
# ─────────────────────────────────────────────
echo -e "${GREEN}[1/5] Проверка Ollama...${NC}"

if ! command -v ollama &> /dev/null; then
    echo -e "${YELLOW}Ollama не найдена. Установка...${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install ollama
        else
            echo -e "${RED}Homebrew не найден. Установите Ollama вручную: https://ollama.com${NC}"
            exit 1
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -fsSL https://ollama.com/install.sh | sh
    else
        echo -e "${RED}ОС не поддерживается. Установите Ollama вручную: https://ollama.com${NC}"
        exit 1
    fi
    echo -e "${GREEN}Ollama установлена.${NC}"
else
    echo -e "${GREEN}Ollama найдена.${NC}"
fi

# Запуск Ollama сервера
if ! curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo -e "${BLUE}Запуск Ollama сервера...${NC}"
    ollama serve > /dev/null 2>&1 &
    sleep 3
    # Ждём пока сервер станет доступен
    for i in {1..10}; do
        if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
fi

# Скачивание модели
OLLAMA_MODEL=$(grep OLLAMA_MODEL .env 2>/dev/null | cut -d '=' -f2 | tr -d ' ')
OLLAMA_MODEL=${OLLAMA_MODEL:-"qwen2.5:14b"}

if ! ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    echo -e "${YELLOW}Модель $OLLAMA_MODEL не найдена. Скачивание (~9 ГБ)...${NC}"
    ollama pull "$OLLAMA_MODEL"
fi
echo -e "${GREEN}Модель $OLLAMA_MODEL готова.${NC}"

# ─────────────────────────────────────────────
# 2. Python venv и зависимости
# ─────────────────────────────────────────────
echo -e "\n${GREEN}[2/5] Проверка Python-зависимостей...${NC}"

if ! command -v python3 &> /dev/null && ! command -v python &> /dev/null; then
    echo -e "${RED}Python не найден. Установите Python 3.10+${NC}"
    exit 1
fi

# Проверка версии Python (рекомендуем 3.10+)
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    PYTHON_CMD="python"
fi
PY_VERSION=$($PYTHON_CMD -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
PY_MAJOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.major)" 2>/dev/null)
PY_MINOR=$($PYTHON_CMD -c "import sys; print(sys.version_info.minor)" 2>/dev/null)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo -e "${YELLOW}⚠ Python $PY_VERSION обнаружен. Рекомендуется Python 3.10+${NC}"
    echo -e "${YELLOW}  Некоторые пакеты могут не иметь готовых wheel для Python $PY_VERSION${NC}"
fi

if [ ! -x "./venv/bin/python" ]; then
    echo -e "${BLUE}Создание виртуального окружения...${NC}"
    $PYTHON_CMD -m venv venv || { echo -e "${RED}Не удалось создать venv!${NC}"; exit 1; }
fi

# Используем python -m pip (обходит проблему сломанного shebang в pip при переносе проекта)
./venv/bin/python -m pip install --upgrade pip -q 2>/dev/null
./venv/bin/python -m pip install -q -r requirements.txt
echo -e "${GREEN}Python-зависимости готовы.${NC}"

# ─────────────────────────────────────────────
# 3. Frontend зависимости
# ─────────────────────────────────────────────
echo -e "\n${GREEN}[3/5] Проверка фронтенд-зависимостей...${NC}"

# Устанавливаем Node.js/npm если не найдены
if ! command -v npm &> /dev/null; then
    echo -e "${YELLOW}npm не найден. Установка Node.js...${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &> /dev/null; then
            brew install node
        else
            echo -e "${RED}Homebrew не найден. Установите Node.js: https://nodejs.org${NC}"
            exit 1
        fi
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null
        apt-get install -y nodejs 2>/dev/null || yum install -y nodejs 2>/dev/null
    fi
fi

if ! command -v npm &> /dev/null; then
    echo -e "${RED}npm не найден. Установите Node.js: https://nodejs.org${NC}"
    exit 1
fi

if [ ! -d "frontend/node_modules" ] || [ "frontend/package.json" -nt "frontend/node_modules/.package-lock.json" ]; then
    echo -e "${BLUE}Установка npm-зависимостей...${NC}"
    (cd frontend && npm install --silent) || { echo -e "${RED}npm install не удался!${NC}"; exit 1; }
fi
echo -e "${GREEN}Фронтенд-зависимости готовы.${NC}"

# ─────────────────────────────────────────────
# 4. Конфигурация
# ─────────────────────────────────────────────
echo -e "\n${GREEN}[4/5] Проверка конфигурации...${NC}"

if [ ! -f ".env" ]; then
    echo -e "${BLUE}Создание .env из .env.example...${NC}"
    if [ -f ".env.example" ]; then
        cp .env.example .env
    fi
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}Не удалось скопировать .env.example, создаю .env с дефолтами...${NC}"
        NEW_KEY=$(./venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
        cat > .env << EOF
SECRET_KEY=$NEW_KEY
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
EOF
        echo -e "${GREEN}SECRET_KEY сгенерирован.${NC}"
    fi
    # Генерируем SECRET_KEY если его нет
    if [ -f ".env" ] && (grep -q "SECRET_KEY=$" .env || grep -q 'SECRET_KEY=""' .env); then
        NEW_KEY=$(./venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s/SECRET_KEY=.*/SECRET_KEY=$NEW_KEY/" .env
        else
            sed -i "s/SECRET_KEY=.*/SECRET_KEY=$NEW_KEY/" .env
        fi
        echo -e "${GREEN}SECRET_KEY сгенерирован.${NC}"
    fi
fi
echo -e "${GREEN}Конфигурация готова.${NC}"

# ─────────────────────────────────────────────
# 5. Запуск сервисов
# ─────────────────────────────────────────────
echo -e "\n${GREEN}[5/5] Запуск сервисов...${NC}"

# Освобождаем порты
for port in 8000 5173; do
    if lsof -i :$port -t &> /dev/null; then
        lsof -i :$port -t | xargs kill -9 2>/dev/null
        sleep 0.5
    fi
done

# Бэкенд
mkdir -p logs

# Предварительная проверка импортов (ловит ошибки ДО запуска сервера)
echo -e "${BLUE}  Проверка зависимостей бэкенда...${NC}"
if ! ./venv/bin/python -c "
import sys
try:
    import duckdb
    import fastapi
    import pandas
    import pydantic
    from app.core.config import settings
    from app.db.connection import DatabaseManager
    print(f'OK: Python {sys.version_info.major}.{sys.version_info.minor}, duckdb {duckdb.__version__}')
except Exception as e:
    print(f'IMPORT ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1; then
    echo -e "${RED}ОШИБКА: Не удалось импортировать модули бэкенда!${NC}"
    echo -e "${YELLOW}Попробуйте: rm -rf venv && ./start.sh${NC}"
    exit 1
fi

echo -e "${BLUE}  Запуск бэкенда (порт 8000)...${NC}"
./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > logs/backend.log 2>&1 &
BACKEND_PID=$!

# Ждём пока бэкенд стартует (Ollama warmup может занять до 60 сек)
BACKEND_OK=false
for i in {1..60}; do
    # Проверяем, не упал ли процесс
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo -e "${RED}ОШИБКА: Бэкенд завершился с ошибкой!${NC}"
        echo -e "${YELLOW}Полный лог: logs/backend.log${NC}"
        echo -e "${YELLOW}────────────────────────────────────${NC}"
        cat logs/backend.log 2>/dev/null || echo "(лог пуст)"
        echo -e "${YELLOW}────────────────────────────────────${NC}"
        exit 1
    fi
    if curl -s http://127.0.0.1:8000/health > /dev/null 2>&1; then
        BACKEND_OK=true
        break
    fi
    sleep 1
done

if [ "$BACKEND_OK" = false ]; then
    echo -e "${RED}ОШИБКА: Бэкенд не стартовал за 60 секунд!${NC}"
    echo -e "${YELLOW}Полный лог: logs/backend.log${NC}"
    echo -e "${YELLOW}────────────────────────────────────${NC}"
    tail -50 logs/backend.log 2>/dev/null || echo "(лог пуст)"
    echo -e "${YELLOW}────────────────────────────────────${NC}"
    exit 1
fi

# Фронтенд
echo -e "${BLUE}  Запуск фронтенда (порт 5173)...${NC}"
cd frontend && npm run dev > ../logs/frontend.log 2>&1 &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"

sleep 3

# Проверяем что фронтенд отвечает
FRONTEND_OK=false
for i in {1..10}; do
    if curl -s http://127.0.0.1:5173 > /dev/null 2>&1; then
        FRONTEND_OK=true
        break
    fi
    sleep 1
done

if [ "$FRONTEND_OK" = false ]; then
    echo -e "${RED}ОШИБКА: Фронтенд не стартовал!${NC}"
    echo -e "${YELLOW}Логи: logs/frontend.log${NC}"
    tail -20 logs/frontend.log 2>/dev/null || echo "(лог пуст)"
    exit 1
fi

# Открытие браузера
echo -e "${BLUE}  Открытие браузера...${NC}"
if [[ "$OSTYPE" == "darwin"* ]]; then
    open http://localhost:5173
elif command -v xdg-open &> /dev/null; then
    xdg-open http://localhost:5173
elif command -v wslview &> /dev/null; then
    wslview http://localhost:5173
fi

echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}  Все сервисы запущены!${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "  Фронтенд:  ${BLUE}http://localhost:5173${NC}"
echo -e "  Бэкенд:    ${BLUE}http://localhost:8000${NC}"
echo -e "  Логи:      ${BLUE}logs/${NC}"
echo -e "  Для остановки: ${YELLOW}Ctrl+C${NC}"
echo -e "${GREEN}======================================================${NC}\n"

# Ожидаем
wait
