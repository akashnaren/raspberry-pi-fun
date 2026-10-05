"""Load Pro into Ollama at brain startup. This does not pull the tag.

keep_alive stays the pi4 knob (-1 by default). A missing tag is logged and
the process still serves Flash.
"""

from __future__ import annotations

import json
import threading
import urllib.request

from pair.embed import ollama_base, on_pi4
from pair.knobs import keep_alive
from pair.modes import mode_table

PRELOAD_TIMEOUT_S = 3.0


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
        with urllib.request.urlopen(request, timeout=PRELOAD_TIMEOUT_S) as response:
            response.read()
        print(f"pro preload: {payload['model']}", flush=True)
    except Exception as exc:
        print(f"pro preload skipped: {exc}", flush=True)


def start_pro_warm() -> threading.Thread:
    """Daemon preload so listen is not blocked on the Pro weights."""

    def run() -> None:
        try:
            warm_pro_model()
        except Exception as exc:
            print(f"pro preload skipped: {exc}", flush=True)

    thread = threading.Thread(target=run, name="pro-preload", daemon=True)
    thread.start()
    return thread
