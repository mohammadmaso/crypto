#!/usr/bin/env bash
# Installs/updates the launchd agent (starts at login, restarts on crash, catch-up handles missed runs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.nobimomentum.plist"
mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
sed "s#__ROOT__#$ROOT#g" "$ROOT/deploy/com.nobimomentum.plist" > "$DEST"
launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"
echo "Installed. Panel: http://127.0.0.1:8000   Logs: $ROOT/logs/"
