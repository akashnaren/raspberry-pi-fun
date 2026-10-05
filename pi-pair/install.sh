#!/usr/bin/env bash
# Fleet chat installer — run on each Raspberry Pi (pi2 / pi3 / pi4).
# Does NOT prompt for a sudo password: prints the commands you need.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${PI_PAIR_DIR:-$HOME/pi-pair}"
SERVICE_NAME="pi-pair"
OLLAMA_MODEL_PRIMARY="qwen2.5:0.5b"
OLLAMA_EMBED_MODEL="snowflake-arctic-embed:m"
PAIR_PORT="${PI_PAIR_PORT:-18080}"
NODE_NAME="${PI_PAIR_NAME:-$(hostname -s)}"

ROLE="dataset"
if [[ "$NODE_NAME" == *pi2* ]]; then
  ROLE="health"
elif [[ "$NODE_NAME" == *pi3* ]]; then
  ROLE="dataset"
elif [[ "$NODE_NAME" == *pi4* ]]; then
  ROLE="brain"
fi

echo "=== fleet chat install ==="
echo "Source:  $ROOT"
echo "Target:  $INSTALL_DIR"
echo "Name:    $NODE_NAME"
echo "Role:    $ROLE"
echo "Proxy:   0.0.0.0:${PAIR_PORT}"
if ! command -v tesseract >/dev/null 2>&1 || ! command -v pdftoppm >/dev/null 2>&1; then
  echo "Attachment OCR needs local binaries (sudo once, no cloud OCR API):"
  echo "  sudo apt-get install -y tesseract-ocr poppler-utils"
fi
if [[ "$ROLE" == "brain" ]]; then
  echo "Ollama:  0.0.0.0:11434 on this board only"
  echo "Embed:   ${OLLAMA_EMBED_MODEL} for map paraphrases on this board only"
else
  echo "Ollama:  not installed here. Chat and embed models run only on pi4."
fi
echo

mkdir -p "$INSTALL_DIR/pair" "$INSTALL_DIR/static" \
  "$INSTALL_DIR/scripts/lifecycle" "$INSTALL_DIR/scripts/data" "$INSTALL_DIR/scripts/train" "$INSTALL_DIR/scripts/eval" \
  "$INSTALL_DIR/configs/train" "$INSTALL_DIR/configs/runtime" \
  "$INSTALL_DIR/docs" \
  "$INSTALL_DIR/data/canned" "$INSTALL_DIR/data/seed" \
  "$INSTALL_DIR/data/train/pending" "$INSTALL_DIR/data/train/active" "$INSTALL_DIR/data/train/done" \
  "$INSTALL_DIR/data/prepared" \
  "$INSTALL_DIR/adapters/staging" "$INSTALL_DIR/adapters/active"

cp -f "$ROOT/mini_chat.py" "$ROOT/start.sh" "$ROOT/mesh-hello.sh" "$INSTALL_DIR/"
readme_src="$ROOT/README.md"
if [[ ! -f "$readme_src" && -f "$ROOT/../README.md" ]]; then
  readme_src="$ROOT/../README.md"
fi
if [[ -f "$readme_src" ]]; then
  cp -f "$readme_src" "$INSTALL_DIR/README.md"
fi
cp -f "$ROOT/pair/"*.py "$INSTALL_DIR/pair/"
cp -f "$ROOT/static/"* "$INSTALL_DIR/static/"
cp -a "$ROOT/scripts/." "$INSTALL_DIR/scripts/"
cp -a "$ROOT/configs/." "$INSTALL_DIR/configs/"
cp -a "$ROOT/docs/." "$INSTALL_DIR/docs/"
chmod +x "$INSTALL_DIR/mini_chat.py" "$INSTALL_DIR/start.sh" "$INSTALL_DIR/mesh-hello.sh" \
  "$INSTALL_DIR/scripts/lifecycle/"*.py "$INSTALL_DIR/scripts/data/"*.py \
  "$INSTALL_DIR/scripts/train/"*.py "$INSTALL_DIR/scripts/eval/"*.py

if [[ ! -f "$INSTALL_DIR/peers.json" ]]; then
  cp "$ROOT/peers.example.json" "$INSTALL_DIR/peers.json"
  echo "Wrote $INSTALL_DIR/peers.json — edit hosts if this LAN map is wrong."
else
  echo "Keeping existing $INSTALL_DIR/peers.json"
fi
cp -f "$ROOT/peers.example.json" "$INSTALL_DIR/peers.example.json"

cp -f "$ROOT/data/dataset_info.json" "$INSTALL_DIR/data/dataset_info.json"
cp -a "$ROOT/data/seed/." "$INSTALL_DIR/data/seed/"
if [[ ! -f "$INSTALL_DIR/data/canned/canned_map.json" ]]; then
  cp -f "$ROOT/data/canned/canned_map.json" "$INSTALL_DIR/data/canned/canned_map.json"
  echo "Installed canned map."
else
  echo "Keeping existing canned map."
fi
if [[ ! -f "$INSTALL_DIR/data/canned/canned_seed.jsonl" ]]; then
  cp -f "$ROOT/data/canned/canned_seed.jsonl" "$INSTALL_DIR/data/canned/canned_seed.jsonl"
fi

if [[ "$ROLE" != "brain" ]]; then
  echo "Skipping model pull on ${NODE_NAME}: chat and embed models run only on pi4."
else
  if ! command -v ollama >/dev/null 2>&1; then
    echo
    echo "Ollama not found. Install with (needs network + sudo once):"
    echo "  curl -fsSL https://ollama.com/install.sh | sh"
    echo
    echo "Then re-run: bash $ROOT/install.sh"
  else
    echo "Ollama present: $(command -v ollama)"
    echo "Pulling ${OLLAMA_MODEL_PRIMARY}…"
    ollama pull "$OLLAMA_MODEL_PRIMARY" || echo "WARN: model pull failed — pull manually later on pi4."
    echo "Pulling ${OLLAMA_EMBED_MODEL}…"
    ollama pull "$OLLAMA_EMBED_MODEL" || echo "WARN: embed model pull failed — pull manually later on pi4."
  fi
  echo
  echo "--- Make Ollama listen on LAN (run these yourself if needed) ---"
  cat << 'SUDO'
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'DROPIN'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=3"
Environment="OLLAMA_KEEP_ALIVE=-1"
DROPIN
sudo systemctl daemon-reload
sudo systemctl restart ollama
SUDO
  echo
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
if [[ "$ROLE" == "brain" ]]; then
  # Live pi4 runs this user unit, not the system ollama.service. Refresh the
  # drop-in on every install so a reinstall cannot fall back to one loaded
  # model. Leave an existing unit's ExecStart alone.
  LAN_DROP="$UNIT_DIR/ollama-lan.service.d"
  mkdir -p "$LAN_DROP"
  cat > "$LAN_DROP/resident.conf" << 'EOF'
[Service]
Environment=OLLAMA_MAX_LOADED_MODELS=3
Environment=OLLAMA_KEEP_ALIVE=-1
EOF
  LAN_UNIT="$UNIT_DIR/ollama-lan.service"
  if [[ ! -f "$LAN_UNIT" ]]; then
    cp -f "$ROOT/configs/runtime/ollama-lan.service" "$LAN_UNIT"
    echo "Wrote $LAN_UNIT"
  else
    echo "Keeping existing $LAN_UNIT"
  fi
  echo "Refreshed $LAN_DROP/resident.conf"
  echo "ollama-lan picks up OLLAMA_MAX_LOADED_MODELS=3 and OLLAMA_KEEP_ALIVE=-1 the next time that user unit starts."
fi
UNIT_FILE="$UNIT_DIR/${SERVICE_NAME}.service"
cat > "$UNIT_FILE" << UNIT
[Unit]
Description=Pi GPT 1.0
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=PI_PAIR_NAME=$NODE_NAME
Environment=PI_PAIR_ROLE=$ROLE
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
if [[ "$ROLE" == "dataset" ]]; then
  echo
  echo "Train-then-delete (pi3 only, after the queue has misses):"
  echo "  python3 $INSTALL_DIR/scripts/lifecycle/post_train.py"
fi
echo
echo "Done. Chat UI: http://<this-pi-ip>:${PAIR_PORT}/"
