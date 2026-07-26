#!/bin/bash
# Download Ollama binary for bundling in desktop app
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
OLLAMA_DIR="$DIR/resources/ollama"
ARCH=$(uname -m)
TEMP_DIR=$(mktemp -d)

echo "=== Downloading Ollama binary for $ARCH ==="

mkdir -p "$OLLAMA_DIR/$ARCH"

# Get latest release version from GitHub
OLLAMA_VERSION=$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest | grep '"tag_name"' | sed -E 's/.*"v([^"]+)".*/\1/')
echo "[download] Latest Ollama version: $OLLAMA_VERSION"

# Download the tgz archive
OLLAMA_URL="https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-darwin.tgz"
echo "[download] Downloading from: $OLLAMA_URL"

curl -L -o "$TEMP_DIR/ollama.tgz" "$OLLAMA_URL"
echo "[download] Extracting..."
tar -xzf "$TEMP_DIR/ollama.tgz" -C "$TEMP_DIR"

# Find the ollama binary in extracted contents
OLLAMA_BIN=$(find "$TEMP_DIR" -name "ollama" -type f | head -1)
if [ -z "$OLLAMA_BIN" ]; then
  echo "[download] ERROR: ollama binary not found in archive"
  ls -la "$TEMP_DIR"
  exit 1
fi

cp "$OLLAMA_BIN" "$OLLAMA_DIR/$ARCH/ollama"
chmod +x "$OLLAMA_DIR/$ARCH/ollama"

# Cleanup
rm -rf "$TEMP_DIR"

echo "[download] Ollama binary saved to: $OLLAMA_DIR/$ARCH/ollama"
ls -lh "$OLLAMA_DIR/$ARCH/ollama"

echo ""
echo "=== Done ==="
