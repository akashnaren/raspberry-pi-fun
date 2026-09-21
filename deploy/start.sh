#!/bin/bash
# Resolve user-local Node (versioned prefix or ~/.local/node symlink) and start.
# Order: $HOME/.local/node-v20.20.2  then  ~/.local/node symlink  then  PATH.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

resolve_node() {
  local candidate
  for candidate in \
    "${HOME}/.local/node-v20.20.2/bin/node" \
    "${HOME}/.local/node/bin/node"
  do
    if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  if command -v node >/dev/null 2>&1; then
    command -v node
    return 0
  fi
  echo "fishbowl: Node not found. Install v20.20.2 to \$HOME/.local/node-v20.20.2 or symlink ~/.local/node" >&2
  return 127
}

NODE="$(resolve_node)" || exit 127

if [ "${FISHBOWL_RESOLVE_ONLY:-}" = "1" ]; then
  printf '%s\n' "${NODE}"
  exit 0
fi

export PATH="$(dirname "${NODE}"):${PATH}"
# DRY_RUN defaults true in src/. Secrets come from optional EnvironmentFile paths.
exec "${NODE}" src/index.js
