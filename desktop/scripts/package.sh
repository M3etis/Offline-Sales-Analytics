#!/bin/bash
# Full desktop packaging pipeline
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Модель для встраивания: --model current|none|<имя из списка рекомендуемых> (или BUNDLE_MODEL=...).
# Без параметра скрипт спросит интерактивно.
MODEL_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL_ARGS=(--model "$2"); shift 2 ;;
    --model=*) MODEL_ARGS=(--model "${1#--model=}"); shift ;;
    *) echo "Unknown option: $1"; echo "Usage: package.sh [--model current|none|<model>]"; exit 1 ;;
  esac
done

echo "=== Desktop Packaging Pipeline ==="
echo ""

echo "[1/6] Downloading Ollama binary..."
bash "$DIR/scripts/download-ollama.sh"

echo ""
echo "[2/6] Bundling model..."
bash "$DIR/scripts/bundle-model.sh" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"}

echo ""
echo "[3/6] Building frontend..."
bash "$DIR/scripts/build-frontend.sh"

echo ""
echo "[4/6] Building backend binary..."
bash "$DIR/scripts/build-backend.sh"

echo ""
echo "[5/6] Compiling Electron TypeScript..."
cd "$DIR"
npx tsc -p tsconfig.json

echo ""
echo "[6/6] Packaging Electron app..."
npx electron-builder --config electron-builder.yml

echo ""
echo "=== Done! Desktop app in $DIR/release ==="
