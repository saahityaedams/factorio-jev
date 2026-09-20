#!/usr/bin/env bash
set -euo pipefail

exec /Applications/factorio.app/Contents/MacOS/factorio \
  --config "/Users/saahitya/Library/Application Support/factorio-client-config/config.ini" \
  --mp-connect 127.0.0.1
