#!/usr/bin/env bash
# Fleet chat installer — run on each Raspberry Pi (pi2 / pi3 / pi4).
# Does NOT prompt for a sudo password: prints the commands you need.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${PI_PAIR_DIR:-$HOME/pi-pair}"
SERVICE_NAME="pi-pair"
OLLAMA_MODEL_PRIMARY="qwen3:0.6b"
OLLAMA_PRO_MODEL="qwen3:1.7b"
REMOVED_EMBED="snowflake-arctic-embed:m"
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
  echo "Search:  pi2 first, then local DuckDuckGo if pi2 is down. Generation stays here."
elif [[ "$ROLE" == "health" ]]; then
  echo "Ollama:  not installed here. Chat models run only on pi4."
  echo "Search:  POST /v1/search on this board. No decode."
elif [[ "$ROLE" == "dataset" ]]; then
  echo "Ollama:  not installed here. Chat models run only on pi4."
  echo "Labels:  HMAC votes stay on this board. Do not set HF_TOKEN until a public dataset is approved. No decode."
else
  echo "Ollama:  not installed here. Chat models run only on pi4."
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

parallel_from_config() {
  python3 - "$1" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    row = json.load(open(path, encoding="utf-8"))
    value = int(row.get("ollama_num_parallel", 2))
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    value = 2
if value < 1:
    value = 1
if value > 4:
    value = 4
print(value)
PY
}
read_tag() {
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

path, key, fallback = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    row = json.load(open(path, encoding="utf-8"))
    value = str(row.get(key) or "").strip()
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    value = ""
print(value or fallback)
PY
}
INFERENCE_CFG="$ROOT/configs/runtime/inference_pi4.json"
OLLAMA_MODEL_PRIMARY="$(read_tag "$INFERENCE_CFG" model "$OLLAMA_MODEL_PRIMARY")"
OLLAMA_PRO_MODEL="$(read_tag "$INFERENCE_CFG" pro_model "$OLLAMA_PRO_MODEL")"
OLLAMA_NUM_PARALLEL="$(parallel_from_config "$INFERENCE_CFG")"

if [[ "$ROLE" != "brain" ]]; then
  echo "Skipping model pull on ${NODE_NAME}: chat models run only on pi4."
else
  if ! command -v ollama >/dev/null 2>&1; then
    echo
    echo "Ollama not found. Install with (needs network + sudo once):"
    echo "  curl -fsSL https://ollama.com/install.sh | sh"
    echo
    echo "Then re-run: bash $ROOT/install.sh"
  else
    echo "Ollama present: $(command -v ollama)"
    echo "Pulling ${OLLAMA_MODEL_PRIMARY} (Flash). Pro is not pulled."
    ollama pull "$OLLAMA_MODEL_PRIMARY" || echo "WARN: model pull failed — pull manually later on pi4."
    if ollama show "$REMOVED_EMBED" >/dev/null 2>&1; then
      echo "Removing ${REMOVED_EMBED}."
      ollama rm "$REMOVED_EMBED" || echo "WARN: could not remove ${REMOVED_EMBED}."
    fi
    echo "Pro is ${OLLAMA_PRO_MODEL}. This script does not pull it. Pro stays on disk for measurement."
    echo "A Flash request does not run ollama pull. If the tag is missing, pull it on pi4 only:"
    echo "  ollama pull ${OLLAMA_PRO_MODEL}"
    echo "Preloading ${OLLAMA_MODEL_PRIMARY} with keep_alive -1."
    if command -v curl >/dev/null 2>&1; then
      flash_body="$(python3 - "$OLLAMA_MODEL_PRIMARY" <<'PY'
import json
import sys

print(json.dumps(
    {
        "model": sys.argv[1],
        "prompt": " ",
        "stream": False,
        "keep_alive":-1,
        "think": False,
        "options": {"num_predict": 1},
    },
    separators=(",", ":"),
))
PY
)"
      curl -fsS http://127.0.0.1:11434/api/generate \
        -H "content-type: application/json" \
        -d "$flash_body" \
        >/dev/null || echo "WARN: Flash preload failed. The server retries it on startup."
    fi
    if ollama show "$OLLAMA_PRO_MODEL" >/dev/null 2>&1; then
      echo "Preloading ${OLLAMA_PRO_MODEL} with keep_alive -1."
      if command -v curl >/dev/null 2>&1; then
        pro_body="$(python3 - "$OLLAMA_PRO_MODEL" <<'PY'
import json
import sys

print(json.dumps(
    {
        "model": sys.argv[1],
        "prompt": " ",
        "stream": False,
        "keep_alive":-1,
        "think": False,
        "options": {"num_predict": 1},
    },
    separators=(",", ":"),
))
PY
)"
        curl -fsS http://127.0.0.1:11434/api/generate \
          -H "content-type: application/json" \
          -d "$pro_body" \
          >/dev/null || echo "WARN: Pro preload failed. The server retries it on startup."
      else
        echo "WARN: curl is missing, so Pro was not preloaded. The server retries it on startup."
      fi
    else
      echo "Pro tag is not on disk yet, so it was not preloaded."
    fi
  fi
  echo
  echo "--- Make Ollama listen on LAN (run these yourself if needed) ---"
  echo "Chat models stay loaded. ${OLLAMA_NUM_PARALLEL} chat sequences share ${OLLAMA_MODEL_PRIMARY}."
  echo "The chat router rejects a further generation with HTTP 503."
  cat << SUDO
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null <<'DROPIN'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}"
Environment="OLLAMA_MAX_QUEUE=${OLLAMA_NUM_PARALLEL}"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_KEEP_ALIVE=-1"
DROPIN
sudo systemctl daemon-reload
sudo systemctl restart ollama
SUDO
  echo
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
API_ENV_LINE=""
REMOTE_SEARCH_LINE=""
HF_ENV_LINE=""
if [[ "$ROLE" == "brain" ]]; then
  # Live pi4 runs this user unit, not the system ollama.service. Refresh the
  # drop-in on every install so a reinstall cannot fall back to one loaded
  # model. Leave an existing unit's ExecStart alone.
  LAN_DROP="$UNIT_DIR/ollama-lan.service.d"
  mkdir -p "$LAN_DROP"
  cat > "$LAN_DROP/resident.conf" << EOF
[Service]
Environment=OLLAMA_MAX_LOADED_MODELS=2
Environment=OLLAMA_KEEP_ALIVE=-1
Environment=OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}
Environment=OLLAMA_MAX_QUEUE=${OLLAMA_NUM_PARALLEL}
EOF
  LAN_UNIT="$UNIT_DIR/ollama-lan.service"
  if [[ ! -f "$LAN_UNIT" ]]; then
    cp -f "$ROOT/configs/runtime/ollama-lan.service" "$LAN_UNIT"
    echo "Wrote $LAN_UNIT"
  else
    echo "Keeping existing $LAN_UNIT"
  fi
  echo "Refreshed $LAN_DROP/resident.conf"
  echo "ollama-lan picks up OLLAMA_MAX_LOADED_MODELS=2, OLLAMA_KEEP_ALIVE=-1, and OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL} the next time that user unit starts."
  # The key stays out of the unit and out of git. The env file is mode 600.
  API_ENV_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/pi-pair"
  API_ENV_FILE="$API_ENV_DIR/pi-gpt-api.env"
  mkdir -p "$API_ENV_DIR"
  if [[ ! -f "$API_ENV_FILE" ]]; then
    printf '%s\n' \
      '# Key for POST /api/chat and GET /api/health. Not stored in git.' \
      'PI_GPT_API_KEY=' > "$API_ENV_FILE"
    echo "Wrote $API_ENV_FILE — set PI_GPT_API_KEY in that file, then restart the user unit."
  else
    echo "Keeping existing $API_ENV_FILE"
  fi
  chmod 600 "$API_ENV_FILE"
  API_ENV_LINE="EnvironmentFile=$API_ENV_FILE"
  REMOTE_SEARCH_LINE="Environment=PI_PAIR_REMOTE_SEARCH=1"
fi
if [[ "$ROLE" == "dataset" ]]; then
  # Empty values skip the upload. The token stays in this mode-600 file, not in git.
  HF_ENV_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/pi-pair"
  HF_ENV_FILE="$HF_ENV_DIR/hf.env"
  mkdir -p "$HF_ENV_DIR"
  if [[ ! -f "$HF_ENV_FILE" ]]; then
    printf '%s\n' \
      '# Public label sync for pi3. Leave every value blank on rollout. Do not commit this file.' \
      '# Do not set HF_TOKEN, and do not create akashnaren/pi-mesh-labels, until Akash approves a public dataset.' \
      '# Mint PI_PAIR_LABEL_PEPPER locally later: 32 bytes as 64 hex digits, or raw text of at least 32 bytes.' \
      'HF_TOKEN=' \
      'KAGGLE_API_TOKEN=' \
      'PI_PAIR_LABEL_PEPPER=' > "$HF_ENV_FILE"
    echo "Wrote $HF_ENV_FILE — leave HF_TOKEN blank. Mint PI_PAIR_LABEL_PEPPER on this board later."
  else
    echo "Keeping existing $HF_ENV_FILE"
  fi
  chmod 600 "$HF_ENV_FILE"
  HF_ENV_LINE="EnvironmentFile=$HF_ENV_FILE"
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
${REMOTE_SEARCH_LINE}
${API_ENV_LINE}
${HF_ENV_LINE}
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
  echo "Public HMAC votes stay local while HF_TOKEN is blank. Do not set HF_TOKEN during rollout."
  echo "  python3 $INSTALL_DIR/scripts/data/sync_mesh_labels.py"
fi
echo
echo "Done. Chat UI: http://<this-pi-ip>:${PAIR_PORT}/"
