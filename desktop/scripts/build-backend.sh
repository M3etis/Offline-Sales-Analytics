#!/bin/bash
# Build backend binary via PyInstaller for macOS
# On Apple Silicon: builds arm64 native binary
# On Intel: builds x64 native binary
# Universal DMG requires building on both architectures and merging
set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DESKTOP="$ROOT/desktop"

PYINSTALLER_ARGS=(
  --name sales-analytics-backend
  --onedir
  --noconfirm
  --clean
  --add-data "app:app"
  --hidden-import uvicorn
  --hidden-import uvicorn.logging
  --hidden-import uvicorn.loops
  --hidden-import uvicorn.loops.auto
  --hidden-import uvicorn.protocols
  --hidden-import uvicorn.protocols.http
  --hidden-import uvicorn.protocols.http.auto
  --hidden-import uvicorn.protocols.websockets
  --hidden-import uvicorn.protocols.websockets.auto
  --hidden-import uvicorn.lifespan
  --hidden-import uvicorn.lifespan.on
  --hidden-import uvicorn.lifespan.off
  --hidden-import app.main
  --hidden-import app.core.config
  --hidden-import app.core.dependencies
  --hidden-import app.core.security
  --hidden-import app.api.router
  --hidden-import app.api.routes.auth
  --hidden-import app.api.routes.data
  --hidden-import app.api.routes.analytics
  --hidden-import app.api.routes.ai
  --hidden-import app.api.routes.sessions
  --hidden-import app.api.routes.settings
  --hidden-import app.api.routes.users
  --hidden-import app.api.routes.voice
  --hidden-import app.api.routes.ws
  --hidden-import app.services.analytics
  --hidden-import app.services.llm
  --hidden-import app.services.streaming
  --hidden-import app.services.ingest
  --hidden-import app.services.preanalysis
  --hidden-import app.services.column_dict
  --hidden-import app.services.voice
  --hidden-import app.db
  --hidden-import app.db.connection
  --hidden-import app.domain
  --hidden-import app.domain.ai
  --hidden-import app.domain.ai.intent_classifier
  --hidden-import app.domain.ai.response_builder
  --hidden-import app.domain.ai.sql_utils
  --hidden-import app.domain.analytics
  --hidden-import app.schemas
  --hidden-import app.schemas.models
  --hidden-import app.shared
  --hidden-import app.shared.prompts
  --hidden-import app.shared.prompts.templates
  --hidden-import app.utils.sql_safety
  --hidden-import app.utils.helpers
  --hidden-import duckdb
  --hidden-import pandas
  --hidden-import openpyxl
  --hidden-import faster_whisper
  --hidden-import edge_tts
  --hidden-import pydantic_settings
  --hidden-import passlib
  --hidden-import bcrypt
  --hidden-import jwt
)

ARCH=$(uname -m)
echo "=== Building backend for $ARCH ==="

if [ -f "$ROOT/venv/bin/activate" ]; then
  source "$ROOT/venv/bin/activate"
fi

pip install -q pyinstaller

cd "$ROOT"

# Clean previous build
rm -rf "$ROOT/dist/sales-analytics-backend" "$ROOT/build/sales-analytics-backend" "$ROOT/sales-analytics-backend.spec"

python -m PyInstaller "${PYINSTALLER_ARGS[@]}" app/main.py

# Move to arch-specific directory
mkdir -p "$DESKTOP/build/backend"
rm -rf "$DESKTOP/build/backend/$ARCH" 2>/dev/null || true
# Force remove .DS_Store if present
find "$DESKTOP/build/backend/$ARCH" -name ".DS_Store" -delete 2>/dev/null || true
rm -rf "$DESKTOP/build/backend/$ARCH"
mv "$ROOT/dist/sales-analytics-backend" "$DESKTOP/build/backend/$ARCH"

# Clean up root build artifacts
rm -rf "$ROOT/dist/sales-analytics-backend" "$ROOT/build/sales-analytics-backend" "$ROOT/sales-analytics-backend.spec"

echo ""
echo "=== Backend build complete ==="
echo "Architecture: $ARCH"
ls -lh "$DESKTOP/build/backend/$ARCH/sales-analytics-backend"
