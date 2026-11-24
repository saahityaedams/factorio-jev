#!/usr/bin/env bash
set -euo pipefail

RCON_PORT=27015
RCON_PASSWORD=agent-env-dev-password

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO_DIR="$HOME/Library/Application Support/factorio/scenarios/agent-env"

# The scenario source of truth lives in the repo; sync it into Factorio's
# scenarios folder so the server always loads the committed version.
mkdir -p "$SCENARIO_DIR"
cp "$REPO_DIR/scenario/agent-env/control.lua" "$SCENARIO_DIR/control.lua"

# server-settings.json disables auto_pause so the game keeps ticking with no
# graphical client connected (the agent drives a script-owned character).
exec /Applications/factorio.app/Contents/MacOS/factorio \
  --start-server-load-scenario agent-env \
  --server-settings "$REPO_DIR/config/server-settings.json" \
  --rcon-port "$RCON_PORT" \
  --rcon-password "$RCON_PASSWORD"
