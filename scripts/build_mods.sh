#!/usr/bin/env bash
# Assemble the mod directory shared by the server and the graphical client.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODS="$REPO_DIR/.run/mods"
rm -rf "$MODS"
mkdir -p "$MODS"
cp -R "$REPO_DIR/mods/agent-api" "$MODS/agent-api"
cat > "$MODS/mod-list.json" <<'JSON'
{"mods": [{"name": "base", "enabled": true}, {"name": "agent-api", "enabled": true}]}
JSON
