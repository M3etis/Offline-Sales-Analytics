#!/bin/bash
# Встраивание модели в desktop-приложение: текущая модель приложения или одна из рекомендуемых.
#   bash scripts/bundle-model.sh                      # интерактивный выбор
#   bash scripts/bundle-model.sh --model current      # текущая модель (настройки приложения → .env)
#   bash scripts/bundle-model.sh --model qwen2.5:7b   # модель из списка рекомендуемых
#   bash scripts/bundle-model.sh --model none         # без модели — скачивается в самом приложении
#   bash scripts/bundle-model.sh --list               # список рекомендуемых моделей
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

# Python из venv проекта умеет читать текущую модель из базы приложения (нужен duckdb)
PYTHON="$ROOT/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

echo "=== Bundling model ==="
"$PYTHON" "$DIR/scripts/bundle-model.py" "$@"
du -sh "$DIR/resources/ollama-models" 2>/dev/null || true
echo "=== Done ==="
