"""Keep Flash and Pro resident. This does not pull either tag.

Startup warms both. keep_alive stays the pi4 knob (-1 by default) on those
warms. If Ollama drops a tag, the same warm runs again. A chat does not
load a tag that /api/ps does not already list.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pair.core.config import ollama_base, on_pi4
from pair.model.http_pool import open_json_request
from pair.model.knobs import keep_alive, mode_limits
from pair.model.modes import mode_table

PRELOAD_TIMEOUT_S = 180.0
COLD_WAIT_S = 60.0
COLD_POLL_S = 0.05
RESIDENT_TIMEOUT_S = 0.6
REWARM_PAUSE_S = 120.0


def resident_tags() -> tuple[str, str]:
    """Flash and Pro tags that must stay loaded."""
    table = mode_table()
    flash = str(table.get("flash") or "").strip()
    pro = str(table.get("pro") or "").strip()
    return flash, pro


def pro_preload_payload(model: str | None = None) -> dict:
    """A one-token generate that leaves this tag resident. keep_alive is never 0."""
    tag = (model or "").strip()
    if not tag:
        _flash, tag = resident_tags()
    if not tag:
        from pair.model.modes import PRO_MODEL

        tag = PRO_MODEL
    alive = keep_alive()
    if alive == 0:
        alive = -1
    from pair.turn.shape import persona_text

    limits = mode_limits(tag)
    return {
        "model": tag,
        "prompt": persona_text(),
        "stream": False,
        "keep_alive": alive,
        "think": False,
        "options": {
            "num_predict": 1,
            "temperature": 0,
            "num_ctx": limits["num_ctx"],
            "num_batch": limits["num_batch"],
            "num_thread": limits["num_thread"],
        },
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


def wait_for_resident(
    host: str, port: int, model: str, timeout: float | None = None
) -> bool:
    """Poll /api/ps until `model` is loaded or the wait budget runs out."""
    limit = COLD_WAIT_S if timeout is None else timeout
    deadline = time.monotonic() + max(0.0, limit)
    tag = (model or "").strip()
    while tag:
        resident = resident_models(host, port)
        if resident is not None and tag in resident:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(COLD_POLL_S)
    return False


def schedule_pro_warm(host: str, port: int, model: str | None = None) -> None:
    """Background /api/generate. The chat itself does not load the tag."""
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


def _decode_idle() -> bool:
    """True when no chat is decoding or waiting. A poll must not run mid-turn."""
    from pair.core import runtime

    return runtime.gate.in_flight() == 0 and runtime.gate.waiting() == 0


def rewarm_pro_if_evicted() -> None:
    """Reload Flash or Pro when /api/ps no longer lists that tag."""
    if not on_pi4():
        return
    if not _decode_idle():
        return
    host, port = _local_endpoint()
    resident = resident_models(host, port)
    if resident is None:
        return
    for tag in resident_tags():
        if tag and tag not in resident:
            warm_model(tag)


def warm_model(model: str | None = None) -> None:
    """POST /api/generate for one resident tag. No-op off the brain."""
    if not on_pi4():
        return
    payload = pro_preload_payload(model)
    if not payload.get("model") or payload.get("keep_alive") == 0:
        return
    request = urllib.request.Request(
        ollama_base().rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with open_json_request(request, PRELOAD_TIMEOUT_S) as response:
            response.read()
        print(f"model preload: {payload['model']}", flush=True)
    except Exception as exc:
        print(f"model preload skipped: {exc}", flush=True)


def warm_pro_model() -> None:
    """POST /api/generate for Pro. No-op off the brain. Failures are logged."""
    _flash, pro = resident_tags()
    warm_model(pro)


def start_pro_warm() -> threading.Thread:
    """Daemon preload so listen is not blocked on either tag."""

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
