#!/usr/bin/env bash
set -euo pipefail

RCON_PORT=27015
RCON_PASSWORD=agent-env-dev-password

exec /Applications/factorio.app/Contents/MacOS/factorio \
  --start-server-load-scenario agent-env \
  --rcon-port "$RCON_PORT" \
  --rcon-password "$RCON_PASSWORD"
