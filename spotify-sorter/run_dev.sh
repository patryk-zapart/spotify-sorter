#!/usr/bin/env bash
# Runs the FastAPI backend and Vite frontend together for local dev.
# Usage: ./run_dev.sh   (from the project root)
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  echo ""
  echo "Stopping..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
}
trap cleanup EXIT

echo "Starting backend on http://127.0.0.1:8000 ..."
(
  cd "$ROOT_DIR/backend"
  if [ ! -d ".venv" ]; then
    python3 -m venv .venv
  fi
  source .venv/bin/activate
  pip install -q -r requirements.txt
  uvicorn app.main:app --reload --port 8000
) &
BACKEND_PID=$!

echo "Starting frontend on http://127.0.0.1:5173 ..."
(
  cd "$ROOT_DIR/frontend"
  if [ ! -d "node_modules" ]; then
    npm install
  fi
  npm run dev
) &
FRONTEND_PID=$!

wait $BACKEND_PID $FRONTEND_PID
