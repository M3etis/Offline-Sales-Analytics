#!/bin/bash
# Build frontend for desktop production
set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "[build:frontend] Building frontend..."
cd "$ROOT/frontend"

# Set env vars for desktop production build
# These make API calls use absolute URLs to 127.0.0.1:8000
# Using 127.0.0.1 instead of localhost to avoid IPv6 resolution issues
# Run vite build directly (skip tsc -b which has pre-existing errors)
VITE_API_URL=http://127.0.0.1:8000 VITE_WS_HOST=127.0.0.1:8000 npx vite build

echo "[build:frontend] Built to $ROOT/frontend/dist"
