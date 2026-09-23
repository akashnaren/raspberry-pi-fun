#!/usr/bin/env bash
# Start Pi 0.2 High from this directory. Stdlib Python only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
exec python3 "$ROOT/mini_chat.py"
