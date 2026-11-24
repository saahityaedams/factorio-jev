#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
"$REPO_DIR/scripts/build_mods.sh"

# Same mod directory as the server so the client can join a modded game.
exec /Applications/factorio.app/Contents/MacOS/factorio \
  --config "/Users/saahitya/Library/Application Support/factorio-client-config/config.ini" \
  --mod-directory "$REPO_DIR/.run/mods" \
  --disable-audio \
  --mp-connect 127.0.0.1
