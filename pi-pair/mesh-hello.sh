#!/usr/bin/env bash
# Probe the local Pi PAIR router, then each peer with the probe that matches its kind.
set -euo pipefail
PORT="${PI_PAIR_PORT:-18080}"
BASE="http://127.0.0.1:${PORT}"
MODEL="${MESH_MODEL:-qwen2.5:0.5b}"

echo "=== Pi PAIR mesh hello (${BASE}) ==="
echo "--- /health ---"
curl -fsS "${BASE}/health"
echo
echo "--- peers ---"
python3 - "${BASE}" << 'PY'
import json
import sys
import urllib.error
import urllib.request

base = sys.argv[1]
with urllib.request.urlopen(base + "/health", timeout=5) as response:
    health = json.loads(response.read().decode())
for peer in health.get("peers") or []:
    kind = peer.get("kind") or "ollama"
    path = "/v1/models" if kind == "llamacpp" else "/api/tags"
    url = f"http://{peer['host']}:{peer['port']}{path}"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            print(f"{peer['name']} {kind} {url} HTTP {response.status}")
    except Exception as error:
        print(f"{peer['name']} {kind} {url} DOWN {error.__class__.__name__}")
PY
echo "--- /v1/chat/completions (non-stream, auto) ---"
curl -sS "${BASE}/v1/chat/completions" \
  -H 'content-type: application/json' \
  -H 'X-Pi-Target: auto' \
  -H 'X-Pi-Mesh: on' \
  -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi in five words.\"}],\"stream\":false,\"max_tokens\":16,\"pi_target\":\"auto\",\"pi_mesh\":\"on\"}"
echo
