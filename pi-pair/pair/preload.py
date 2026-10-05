"""Load Pro into Ollama at brain startup. This does not pull the tag.

keep_alive stays the pi4 knob (-1 by default). A missing tag is logged and
the process still serves Flash.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pair.embed import ollama_base, on_pi4
from pair.http_pool import open_json_request
from pair.knobs import keep_alive
from pair.modes import mode_table

PRELOAD_TIMEOUT_S = 3.0
RESIDENT_TIMEOUT_S = 0.6
REWARM_PAUSE_S = 30.0


def pro_preload_payload(model: str | None = None) -> dict:
    """A one-token generate that leaves Pro resident. keep_alive is never forced to 0."""
    tag = (model or mode_table().get("pro") or "qwen2.5:1.5b").strip() or "qwen2.5:1.5b"
    alive = keep_alive()
    return {
        "model": tag,
        "prompt": " ",
        "stream": False,
        "keep_alive": alive,
        "options": {"num_predict": 1, "temperature": 0},
    }


def resident_models(host: str, port: int) -> list[str] | None:
    """Names from /api/ps. None when the probe fails, so a cold guess is not made."""
    url = f"http://{host}:{int(port)}/api/ps"
    try:
        with urllib.request.urlopen(url, timeout=RESIDENT_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode() or "{}")
    except (
        OSError,
        urllib.error.URLError,
        json.JSONDecodeError,
        TimeoutError,
        ValueError,
    ):
        return None
    if not isinstance(payload, dict):
        return None
    names: list[str] = []
    for row in payload.get("models") or []:
        if isinstance(row, dict):
            name = str(row.get("name") or row.get("model") or "").strip()
        else:
            name = str(row or "").strip()
        if name:
            names.append(name)
    return names


def schedule_pro_warm(host: str, port: int, model: str | None = None) -> None:
    """Background /api/generate so this turn can answer on Flash."""
    payload = pro_preload_payload(model)

    def run() -> None:
        request = urllib.request.Request(
            f"http://{host}:{int(port)}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=PRELOAD_TIMEOUT_S) as response:
                response.read()
        except Exception as exc:
            print(f"pro preload skipped: {exc}", flush=True)

    threading.Thread(target=run, name="pro-rewarm", daemon=True).start()


def _local_endpoint() -> tuple[str, int]:
    parsed = urllib.parse.urlparse(ollama_base())
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 11434)
    return host, int(port)


def rewarm_pro_if_evicted() -> None:
    """Health-ping /api/ps and reload Pro when Ollama dropped it."""
    if not on_pi4():
        return
    host, port = _local_endpoint()
    resident = resident_models(host, port)
    if resident is None:
        return
    tag = str(pro_preload_payload().get("model") or "")
    if not tag or tag in resident:
        return
    warm_pro_model()


def warm_pro_model() -> None:
    """POST /api/generate for Pro. No-op off the brain. Failures are logged."""
    if not on_pi4():
        return
    payload = pro_preload_payload()
    if payload.get("keep_alive") == 0:
        return
    request = urllib.request.Request(
        ollama_base().rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with open_json_request(request, PRELOAD_TIMEOUT_S) as response:
            response.read()
        print(f"pro preload: {payload['model']}", flush=True)
    except Exception as exc:
        print(f"pro preload skipped: {exc}", flush=True)


def start_pro_warm() -> threading.Thread:
    """Daemon preload so listen is not blocked on the Pro weights."""

    def run() -> None:
        while True:
            try:
                rewarm_pro_if_evicted()
            except Exception as exc:
                print(f"pro preload skipped: {exc}", flush=True)
            time.sleep(REWARM_PAUSE_S)

    thread = threading.Thread(target=run, name="pro-preload", daemon=True)
    thread.start()
    return thread
