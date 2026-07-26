#!/bin/bash
# Desktop dev mode launcher (macOS/Linux)
# Starts frontend dev server, backend, and Electron
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

cleanup() {
  echo -e "\n${BLUE}[dev] Stopping services...${NC}"
  kill $FRONTEND_PID 2>/dev/null || true
  wait $FRONTEND_PID 2>/dev/null || true
  echo -e "${GREEN}[dev] Stopped.${NC}"
  exit 0
}
trap cleanup SIGINT SIGTERM

# Kill existing processes on ports
for port in 5173 8000; do
  if lsof -i :$port -t &> /dev/null; then
    echo -e "${BLUE}[dev] Freeing port $port...${NC}"
    lsof -i :$port -t | xargs kill -9 2>/dev/null || true
    sleep 0.5
  fi
done

# Install desktop dependencies if needed
if [ ! -d "$DIR/node_modules" ]; then
  echo -e "${BLUE}[dev] Installing desktop dependencies...${NC}"
  cd "$DIR" && npm install --silent
fi

# Start frontend dev server
echo -e "${BLUE}[dev] Starting frontend dev server (port 5173)...${NC}"
cd "$ROOT/frontend" && npm run dev &
FRONTEND_PID=$!

# Wait for frontend
echo -e "${BLUE}[dev] Waiting for frontend...${NC}"
for i in {1..15}; do
  if curl -s http://127.0.0.1:5173 > /dev/null 2>&1; then
    echo -e "${GREEN}[dev] Frontend ready.${NC}"
    break
  fi
  sleep 1
done

# Start Electron (it will manage the backend)
echo -e "${GREEN}[dev] Starting Electron...${NC}"
cd "$DIR"
./node_modules/.bin/electron dist-electron/main/index.js

# Cleanup on exit
cleanup
