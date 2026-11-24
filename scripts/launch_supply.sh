#!/usr/bin/env bash
# Headless server running the built-in Supply challenge scenario with the
# agent-api mod. Connect the graphical client with ./scripts/launch_client.sh
# (it loads the same mod directory) -- the scenario needs a real player.
set -euo pipefail

RCON_PORT=27015
RCON_PASSWORD=agent-env-dev-password
SCENARIO="${1:-base/supply}"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
"$REPO_DIR/scripts/build_mods.sh"

exec /Applications/factorio.app/Contents/MacOS/factorio \
  --start-server-load-scenario "$SCENARIO" \
  --mod-directory "$REPO_DIR/.run/mods" \
  --server-settings "$REPO_DIR/config/server-settings.json" \
  --rcon-port "$RCON_PORT" \
  --rcon-password "$RCON_PASSWORD"
