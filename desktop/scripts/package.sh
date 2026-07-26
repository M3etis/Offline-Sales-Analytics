#!/bin/bash
# Full desktop packaging pipeline
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Desktop Packaging Pipeline ==="
echo ""

echo "[1/6] Downloading Ollama binary..."
bash "$DIR/scripts/download-ollama.sh"

echo ""
echo "[2/6] Bundling qwen2.5:14b model..."
bash "$DIR/scripts/bundle-model.sh"

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
