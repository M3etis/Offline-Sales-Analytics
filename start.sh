#!/bin/bash

# Цвета
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# -P: физический путь без симлинков — с ним сравнивается cwd процессов из /proc или lsof (is_own_process)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"
# PID Ollama, которую запустил этот скрипт: stop.sh останавливает только её, а не любую на порту 11434
OLLAMA_PID_FILE="$SCRIPT_DIR/logs/ollama.pid"
OLLAMA_PID=""

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
    local code=$?
    trap - EXIT SIGINT SIGTERM
    if [ -n "$(jobs -p)" ]; then
        echo -e "\n${BLUE}Остановка всех сервисов...${NC}"
        jobs -p | xargs kill 2>/dev/null
        echo -e "${GREEN}Приложение остановлено.${NC}"
    fi
    # Своя Ollama — фоновая задача этого скрипта: она остановлена вместе с остальными
    # (или уже завершилась сама), записанный PID больше не нужен
    if [ -n "$OLLAMA_PID" ]; then
        rm -f "$OLLAMA_PID_FILE"
    fi
    exit "$code"
}
# EXIT — тоже: при `exit 1` после запуска бэкенда или Ollama они оставались работать
trap cleanup EXIT
trap 'exit 0' SIGINT SIGTERM

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
    OLLAMA_PID=$!
    mkdir -p logs
    echo "$OLLAMA_PID" > "$OLLAMA_PID_FILE"
    sleep 3
    # Ждём пока сервер станет доступен
    for i in {1..10}; do
        if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
fi

# ─────────────────────────────────────────────
# 2. Python venv и зависимости
# ─────────────────────────────────────────────
echo -e "\n${GREEN}[2/5] Проверка Python-зависимостей...${NC}"

# Поддерживаемые версии: 3.10–3.13. На 3.14+ pandas 2.x падает с segfault
# (np_datetime.is_supported_dtype), поэтому системный python3 берём только
# если он попадает в диапазон.
py_ok() {
    "$1" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)' &>/dev/null
}

# Рабочий venv на подходящей версии — системный Python для запуска не нужен
VENV_OK=""
if [ -x "./venv/bin/python" ] && py_ok "./venv/bin/python"; then
    VENV_OK=1
fi

PYTHON_CMD=""
if [ -z "$VENV_OK" ]; then
    for candidate in python3.12 python3.13 python3.11 python3.10 python3 python; do
        if command -v "$candidate" &> /dev/null && py_ok "$candidate"; then
            PYTHON_CMD="$(command -v "$candidate")"
            break
        fi
    done

    # Нет подходящего в PATH — пробуем интерпретаторы, которыми управляет uv
    if [ -z "$PYTHON_CMD" ] && command -v uv &> /dev/null; then
        for v in 3.12 3.13 3.11; do
            p="$(uv python find "$v" 2>/dev/null)"
            if [ -n "$p" ] && py_ok "$p"; then
                PYTHON_CMD="$p"
                break
            fi
        done
        if [ -z "$PYTHON_CMD" ]; then
            echo -e "${BLUE}Подходящий Python не найден, устанавливаю Python 3.12 через uv...${NC}"
            if uv python install 3.12; then
                p="$(uv python find 3.12 2>/dev/null)"
                if [ -n "$p" ] && py_ok "$p"; then
                    PYTHON_CMD="$p"
                fi
            fi
        fi
    fi

    if [ -z "$PYTHON_CMD" ]; then
        if command -v python3 &> /dev/null; then
            FOUND="$(python3 --version 2>&1)"
        else
            FOUND="python3 не найден"
        fi
        echo -e "${RED}Нужен Python 3.10–3.13, подходящий интерпретатор не найден (в PATH: $FOUND).${NC}"
        echo -e "${RED}Установите, например: brew install python@3.12${NC}"
        exit 1
    fi

    # venv на неподходящей версии или с битым интерпретатором (удалили Python, на котором его создали)
    # пересоздаём: venv поверх висячей ссылки venv/bin/python не создаётся
    if [ -e venv ] || [ -L venv ]; then
        VENV_PY_VERSION=$(./venv/bin/python --version 2>/dev/null || echo "недоступном Python")
        echo -e "${YELLOW}venv создан на ${VENV_PY_VERSION}, а нужен 3.10–3.13 — пересоздаю...${NC}"
        rm -rf venv
    fi

    echo -e "${BLUE}Python: $PYTHON_CMD${NC}"
    echo -e "${BLUE}Создание виртуального окружения...${NC}"
    "$PYTHON_CMD" -m venv venv || { echo -e "${RED}Не удалось создать venv!${NC}"; exit 1; }
else
    echo -e "${BLUE}Python $(./venv/bin/python -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') из venv${NC}"
fi

# Используем python -m pip (обходит проблему сломанного shebang в pip при переносе проекта)
./venv/bin/python -m pip install --upgrade pip -q 2>/dev/null
./venv/bin/python -m pip install -q -r requirements.txt
echo -e "${GREEN}Python-зависимости готовы.${NC}"

# Скачивание модели — после venv: модель, выбранная в интерфейсе, важнее OLLAMA_MODEL из .env
# (то же правило, что у бэкенда; см. scripts/startup_model.py)
OLLAMA_MODEL=$(./venv/bin/python scripts/startup_model.py 2>/dev/null)
OLLAMA_MODEL=${OLLAMA_MODEL:-"qwen2.5:14b"}

if ! ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    echo -e "${YELLOW}Модель $OLLAMA_MODEL не найдена. Скачивание (~9 ГБ)...${NC}"
    ollama pull "$OLLAMA_MODEL"
fi
echo -e "${GREEN}Модель $OLLAMA_MODEL готова.${NC}"

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
fi
# Генерируем SECRET_KEY, если он пустой или остался заглушкой из .env.example: заглушка длиннее
# 32 символов, бэкенд принимал её как настоящий ключ, а она одинакова у всех установок.
# Проверяем и существующий .env, а не только только что созданный
if [ -f ".env" ] && grep -qE '^SECRET_KEY=(""|your-secret-key[^[:space:]]*)?[[:space:]]*$' .env; then
    NEW_KEY=$(./venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/^SECRET_KEY=.*/SECRET_KEY=$NEW_KEY/" .env
    else
        sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$NEW_KEY/" .env
    fi
    echo -e "${GREEN}SECRET_KEY сгенерирован.${NC}"
fi
echo -e "${GREEN}Конфигурация готова.${NC}"

# ─────────────────────────────────────────────
# 5. Запуск сервисов
# ─────────────────────────────────────────────
echo -e "\n${GREEN}[5/5] Запуск сервисов...${NC}"

# Порты: BACKEND_PORT / FRONTEND_PORT из окружения, по умолчанию 8000 / 5173
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-5173}
for var in BACKEND_PORT FRONTEND_PORT; do
    # Без ведущих нулей: bash читает 08000 как восьмеричное число и падает на арифметике
    if ! [[ ${!var} =~ ^[1-9][0-9]{0,4}$ ]] || [ "${!var}" -gt 65535 ]; then
        echo -e "${RED}$var должен быть номером порта 1–65535, получено: '${!var}'${NC}"
        exit 1
    fi
done

# PID процессов, которые слушают порт. Только LISTEN: `lsof -i :PORT` находит и клиентов —
# например, браузер с открытым приложением
listen_pids() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u
}

port_busy() {
    [ -n "$(listen_pids "$1")" ] && return 0
    # Процессы других пользователей lsof без sudo не видит — проверяем подключением. И по ::1:
    # браузер открывает localhost, а он может уйти на ::1 — там порт тоже должен быть свободен
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null || (exec 3<>"/dev/tcp/::1/$1") 2>/dev/null
}

# Рабочая папка процесса. На Linux — /proc/PID/cwd: lsof там часто не установлен, и без него свой
# прошлый бэкенд не находился, а новый стартовал на 8001 рядом с ним и падал на блокировке DuckDB.
# На macOS /proc нет — спрашиваем lsof
proc_cwd() {
    local cwd=""
    [ -d /proc ] && cwd=$(readlink "/proc/$1/cwd" 2>/dev/null)
    if [ -z "$cwd" ] && command -v lsof >/dev/null 2>&1; then
        cwd=$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    fi
    echo "$cwd"
}

# Сам бинарник Vite (.bin/vite или vite/bin/vite.js), за ним пробел или конец строки: «*vite*»
# совпадал и с vitest
VITE_CMD_RE='node_modules/\.bin/vite( |$)|vite/bin/vite\.js( |$)'

# Процесс наш, если это бэкенд, запущенный ровно из папки проекта, или Vite — ровно из её frontend.
# Одного имени мало: `uvicorn app.main:app` бывает и в других проектах. И не «любая папка внутри»:
# git worktree в .claude/worktrees/<имя>/ — другой экземпляр приложения
is_own_process() {
    local pid=$1 cmd cwd
    cmd=$(ps -o command= -p "$pid" 2>/dev/null) || return 1
    if [[ $cmd == *"uvicorn app.main:app"* ]]; then
        cwd=$(proc_cwd "$pid")
        [ "$cwd" = "$SCRIPT_DIR" ]
    elif [[ $cmd =~ $VITE_CMD_RE ]]; then
        cwd=$(proc_cwd "$pid")
        [ "$cwd" = "$SCRIPT_DIR/frontend" ]
    else
        return 1
    fi
}

# Кандидаты в прошлый запуск: слушающие процессы (если есть lsof) и процессы бэкенда и Vite по
# командной строке — pgrep есть и там, где нет lsof, и находит бэкенд, который ещё не открыл порт
candidate_pids() {
    {
        command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP -sTCP:LISTEN -t 2>/dev/null
        pgrep -f "uvicorn app\.main:app" 2>/dev/null
        pgrep -f "$VITE_CMD_RE" 2>/dev/null
    } | sort -u
}

# Останавливает прошлый запуск этого проекта — бэкенд и Vite из этой папки — на ЛЮБОМ порту.
# Только запрошенного порта мало: прошлый запуск мог уйти на запасной (8001, 5174), и оставшийся
# бэкенд держал бы блокировку файла DuckDB — новый падал бы на старте. Чужие процессы не трогаем.
stop_previous_run() {
    local pid own="" alive="" i
    for pid in $(candidate_pids); do
        if is_own_process "$pid"; then
            own="$own $pid"
        fi
    done
    [ -z "$own" ] && return 0
    echo -e "${BLUE}  Останавливаю прошлый запуск этого проекта (PID:$own)...${NC}"
    kill $own 2>/dev/null
    # Ждём завершения самих процессов, а не освобождения порта: uvicorn закрывает сокет
    # раньше, чем закрывает базу, и kill -9 сразу после этого обрывал штатное завершение
    for i in {1..20}; do
        alive=""
        for pid in $own; do
            kill -0 "$pid" 2>/dev/null && alive="$alive $pid"
        done
        [ -z "$alive" ] && return 0
        sleep 0.5
    done
    # За 10 секунд не завершились — добиваем, но только свои процессы
    kill -9 $alive 2>/dev/null
    sleep 0.5
}

# Выбирает порт для сервиса и печатает его в stdout (сообщения — в stderr).
# Чужие процессы не трогаем: раньше kill -9 по порту убивал, например, OrbStack, который
# пробрасывает порты Docker-контейнеров, — вместо этого берём ближайший свободный порт.
# Третий аргумент — порт, который уже отдан другому сервису, но ещё не занят (бэкенд стартует позже).
pick_port() {
    local port=$1 name=$2 reserved=${3:-} pid candidate who max
    candidate=$port
    max=$((port + 50 > 65535 ? 65535 : port + 50))
    while [ "$candidate" = "$reserved" ] || port_busy "$candidate"; do
        candidate=$((candidate + 1))
        if [ "$candidate" -gt "$max" ]; then
            echo -e "${RED}ОШИБКА: ${name}: нет свободного порта в диапазоне $port–$max${NC}" >&2
            return 1
        fi
    done
    if [ "$candidate" != "$port" ]; then
        if [ "$port" = "$reserved" ]; then
            echo -e "${YELLOW}  Порт $port уже отдан другому сервису — ${name} запустится на порту $candidate${NC}" >&2
        else
            who=$(for pid in $(listen_pids "$port"); do ps -o comm= -p "$pid"; done | xargs -n1 basename 2>/dev/null | sort -u | paste -sd, -)
            echo -e "${YELLOW}  Порт $port занят другим приложением${who:+ ($who)} — ${name} запустится на порту $candidate${NC}" >&2
        fi
    fi
    echo "$candidate"
}

stop_previous_run
BACKEND_PORT=$(pick_port "$BACKEND_PORT" "бэкенд") || exit 1
FRONTEND_PORT=$(pick_port "$FRONTEND_PORT" "фронтенд" "$BACKEND_PORT") || exit 1
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"
FRONTEND_URL="http://127.0.0.1:$FRONTEND_PORT"
# Адрес для браузера — localhost, как до версии с выбором порта: вход, выбранный набор данных и
# настройки хранятся в браузере по адресу страницы, и с 127.0.0.1 их пришлось бы задавать заново.
# Безопасно: порт выбран свободным и на 127.0.0.1, и на ::1 (port_busy) — если браузер сначала
# попробует ::1, там никого нет, и он перейдёт на 127.0.0.1, где слушает Vite
OPEN_URL="http://localhost:$FRONTEND_PORT"

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

echo -e "${BLUE}  Запуск бэкенда (порт $BACKEND_PORT)...${NC}"
./venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" > logs/backend.log 2>&1 &
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
    if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
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
echo -e "${BLUE}  Запуск фронтенда (порт $FRONTEND_PORT)...${NC}"
# BACKEND_PORT читает vite.config.ts (прокси /api/v1 и /ws). --strictPort: без него Vite
# молча берёт следующий порт, а проверка ниже стучалась бы в чужое приложение
(cd frontend && BACKEND_PORT="$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT" --strictPort > ../logs/frontend.log 2>&1) &
FRONTEND_PID=$!

sleep 3

# Проверяем что фронтенд отвечает
FRONTEND_OK=false
for i in {1..10}; do
    if ! kill -0 $FRONTEND_PID 2>/dev/null; then
        break
    fi
    if curl -s "$FRONTEND_URL" > /dev/null 2>&1; then
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
    open "$OPEN_URL"
elif command -v xdg-open &> /dev/null; then
    xdg-open "$OPEN_URL"
elif command -v wslview &> /dev/null; then
    wslview "$OPEN_URL"
fi

echo ""
echo -e "${GREEN}======================================================${NC}"
echo -e "${GREEN}  Все сервисы запущены!${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "  Фронтенд:  ${BLUE}$OPEN_URL${NC} (или $FRONTEND_URL)"
echo -e "  Бэкенд:    ${BLUE}$BACKEND_URL${NC}"
echo -e "  Логи:      ${BLUE}logs/${NC}"
echo -e "  Для остановки: ${YELLOW}Ctrl+C${NC}"
echo -e "${GREEN}======================================================${NC}\n"

# Ожидаем
wait
