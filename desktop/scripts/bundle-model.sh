#!/bin/bash
# Bundle qwen2.5:14b model into desktop app
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="$DIR/resources/ollama-models"
SRC_MODELS="$HOME/.ollama/models"

echo "=== Bundling qwen2.5:14b model ==="

# Check if source model exists
MANIFEST="$SRC_MODELS/manifests/registry.ollama.ai/library/qwen2.5/14b"
if [ ! -f "$MANIFEST" ]; then
  echo "ERROR: qwen2.5:14b model not found at $MANIFEST"
  echo "Run: ollama pull qwen2.5:14b"
  exit 1
fi

echo "[bundle] Copying model files..."

# Create target structure
mkdir -p "$MODELS_DIR/manifests/registry.ollama.ai/library/qwen2.5"
mkdir -p "$MODELS_DIR/blobs"

# Copy manifest
cp "$MANIFEST" "$MODELS_DIR/manifests/registry.ollama.ai/library/qwen2.5/14b"
echo "[bundle] Manifest copied."

# Parse manifest to get required blobs
CONFIG_DIGEST=$(cat "$MANIFEST" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['config']['digest'].replace('sha256:','sha256-'))")
echo "[bundle] Config: $CONFIG_DIGEST"

# Copy config blob
cp "$SRC_MODELS/blobs/$CONFIG_DIGEST" "$MODELS_DIR/blobs/"
echo "[bundle] Config blob copied."

# Copy layer blobs
for DIGEST in $(cat "$MANIFEST" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for layer in d['layers']:
    print(layer['digest'].replace('sha256:', 'sha256-'))
"); do
  SIZE=$(ls -lh "$SRC_MODELS/blobs/$DIGEST" | awk '{print $5}')
  echo "[bundle] Copying $DIGEST ($SIZE)..."
  cp "$SRC_MODELS/blobs/$DIGEST" "$MODELS_DIR/blobs/"
done

echo ""
echo "[bundle] Model bundled successfully."
du -sh "$MODELS_DIR"
echo ""
echo "=== Done ==="
