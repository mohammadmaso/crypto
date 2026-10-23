#!/usr/bin/env bash
# Starts the panel: builds the UI if needed, then serves API + UI on 127.0.0.1:8000.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
if [ ! -d "$ROOT/frontend/dist" ]; then
  (cd "$ROOT/frontend" && npm install --silent && npm run build)
fi
cd "$ROOT/backend"
exec uv run uvicorn app.api.main:app --host 127.0.0.1 --port 8000
