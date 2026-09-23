#!/usr/bin/env bash
# Pi 0.2 High installer — run on each Raspberry Pi (pi2 / pi3 / pi4).
# Does NOT prompt for a sudo password: prints the commands you need.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${PI_PAIR_DIR:-$HOME/pi-pair}"
SERVICE_NAME="pi-pair"
OLLAMA_MODEL_PRIMARY="qwen2.5:0.5b"
OLLAMA_MODEL_FALLBACK="tinyllama"
PAIR_PORT="${PI_PAIR_PORT:-18080}"
NODE_NAME="${PI_PAIR_NAME:-$(hostname -s)}"

echo "=== Pi 0.2 High install ==="
echo "Source:  $ROOT"
echo "Target:  $INSTALL_DIR"
echo "Name:    $NODE_NAME"
echo "Proxy:   0.0.0.0:${PAIR_PORT}"
echo "Ollama:  0.0.0.0:11434 (pi2 llama.cpp stays on :8080)"
echo

mkdir -p "$INSTALL_DIR/pair" "$INSTALL_DIR/static"
cp -f "$ROOT/mini_chat.py" "$ROOT/start.sh" "$ROOT/mesh-hello.sh" "$ROOT/README.md" "$INSTALL_DIR/"
cp -f "$ROOT/pair/"*.py "$INSTALL_DIR/pair/"
cp -f "$ROOT/static/"* "$INSTALL_DIR/static/"
chmod +x "$INSTALL_DIR/mini_chat.py" "$INSTALL_DIR/start.sh" "$INSTALL_DIR/mesh-hello.sh"
if [[ ! -f "$INSTALL_DIR/peers.json" ]]; then
  cp "$ROOT/peers.example.json" "$INSTALL_DIR/peers.json"
  echo "Wrote $INSTALL_DIR/peers.json — edit hosts if this LAN map is wrong."
else
  echo "Keeping existing $INSTALL_DIR/peers.json"
fi
cp -f "$ROOT/peers.example.json" "$INSTALL_DIR/peers.example.json"

if ! command -v ollama >/dev/null 2>&1; then
  echo
  echo "Ollama not found. Install with (needs network + sudo once):"
  echo "  curl -fsSL https://ollama.com/install.sh | sh"
  echo
  echo "Then re-run: bash $ROOT/install.sh"
else
  echo "Ollama present: $(command -v ollama)"
fi

echo
echo "--- Make Ollama listen on LAN (run these yourself if needed) ---"
cat << SUDO
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'DROPIN'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
DROPIN
sudo systemctl daemon-reload
sudo systemctl restart ollama
SUDO
echo

if command -v ollama >/dev/null 2>&1; then
  echo "Pulling ${OLLAMA_MODEL_PRIMARY} (fallback ${OLLAMA_MODEL_FALLBACK})…"
  if ! ollama pull "$OLLAMA_MODEL_PRIMARY"; then
    echo "Primary pull failed; trying $OLLAMA_MODEL_FALLBACK"
    ollama pull "$OLLAMA_MODEL_FALLBACK" || echo "WARN: model pull failed — pull manually later."
  fi
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
UNIT_FILE="$UNIT_DIR/${SERVICE_NAME}.service"
cat > "$UNIT_FILE" << UNIT
[Unit]
Description=Pi 0.2 High
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=PI_PAIR_NAME=$NODE_NAME
Environment=PI_PAIR_HOST=0.0.0.0
Environment=PI_PAIR_PORT=$PAIR_PORT
Environment=PI_PAIR_PEERS=$INSTALL_DIR/peers.json
Environment=MESH_MODEL=${OLLAMA_MODEL_PRIMARY}
ExecStart=$(command -v python3) $INSTALL_DIR/mini_chat.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT

echo "Wrote $UNIT_FILE"
echo
echo "--- Enable user service (no sudo) ---"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now ${SERVICE_NAME}.service"
echo "  systemctl --user status ${SERVICE_NAME}.service"
echo
echo "If user services stop at logout, also run (needs sudo once):"
echo "  sudo loginctl enable-linger \$USER"
echo
echo "Manual start (no systemd):"
echo "  python3 $INSTALL_DIR/mini_chat.py"
echo
echo "Done. Chat UI: http://<this-pi-ip>:${PAIR_PORT}/"
