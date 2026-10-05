"""Non-streaming chat against Ollama and llama.cpp."""

from __future__ import annotations

import json
import threading
import urllib.request

from pair import runtime
from pair.embed import EMBED_MODEL, embed_texts, on_pi4
from pair.guard import may_generate, require_generative
from pair.http_pool import open_json_request
from pair.knobs import inference_knobs, keep_alive, ollama_options
from pair.modes import FLASH, PRO, mode_table

_WARM_THREAD: threading.Thread | None = None


def open_json(url: str, payload: dict, timeout: float):
    """POST JSON. Chat, stream, and the startup warm share this opener."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    return open_json_request(request, timeout)


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    with open_json(url, payload, timeout) as response:
        return json.loads(response.read().decode())


def ollama_payload(
    model, messages, temperature, max_tokens, stream: bool, knobs=None
) -> dict:
    """The one Ollama chat body. keep_alive is the pi4 knob, not a per-call TTL."""
    row = inference_knobs() if knobs is None else knobs
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": keep_alive(row),
        "options": ollama_options(temperature, max_tokens, row, model),
    }


def llamacpp_model(peer, model: str) -> str:
    models = peer.get("models") or []
    if models and (":" in model or model not in models):
        return models[0]
    return model


def chat_ollama(
    peer, model, messages, temperature=0.7, max_tokens=256, meta: dict | None = None
):
    require_generative(peer)
    knobs = inference_knobs()
    url = f"http://{peer['host']}:{peer['port']}/api/chat"
    payload = ollama_payload(model, messages, temperature, max_tokens, False, knobs)
    out = _post_json(url, payload, timeout=180)
    if meta is not None:
        meta["done_reason"] = str(out.get("done_reason") or "")
    text = (out.get("message") or {}).get("content") or out.get("response") or ""
    return text, model


def chat_llamacpp(
    peer, model, messages, temperature=0.7, max_tokens=256, meta: dict | None = None
):
    require_generative(peer)
    use = llamacpp_model(peer, model)
    url = f"http://{peer['host']}:{peer['port']}/v1/chat/completions"
    payload = {
        "model": use,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    out = _post_json(url, payload, timeout=300)
    choice = (out.get("choices") or [{}])[0]
    if meta is not None:
        meta["done_reason"] = str(choice.get("finish_reason") or "")
    content = (choice.get("message") or {}).get("content") or ""
    return content, use


def warm_chat_model(peer: dict, model: str, timeout: float = 45) -> bool:
    """Load one tag with keep_alive -1. A missing tag returns False and is not pulled."""
    if not may_generate(peer) or not model:
        return False
    url = f"http://{peer['host']}:{peer['port']}/api/chat"
    payload = ollama_payload(
        model,
        [{"role": "user", "content": "ok"}],
        0.0,
        1,
        False,
    )
    try:
        with open_json(url, payload, timeout) as response:
            response.read()
        return True
    except Exception:
        return False


def warm_residents(peer: dict, timeout: float = 45) -> list[str]:
    """Load Flash, then Pro if it is already on disk, then ping the embedder.

    Pro is not pulled. A 404 or a down socket is skipped. Off a generative
    peer this returns without a request.
    """
    if not may_generate(peer):
        return []
    table = mode_table()
    loaded: list[str] = []
    for name in (table[FLASH], table[PRO]):
        if name and warm_chat_model(peer, name, timeout=timeout):
            loaded.append(name)
    try:
        if embed_texts(["."]):
            loaded.append(EMBED_MODEL)
    except Exception:
        pass
    return loaded


def warm_in_flight() -> threading.Thread | None:
    """The startup warm, if it has not finished. Request search can overlap it."""
    thread = _WARM_THREAD
    if thread is not None and thread.is_alive():
        return thread
    return None


def start_model_warm(after: threading.Thread | None = None) -> threading.Thread:
    """Load Flash, Pro, and the embedder after the canned-key batch.

    The thread does not block accept. A failure is logged and ignored.
    `after` is joined first so the embed batch is not racing the chat loads.
    """
    global _WARM_THREAD

    def run() -> None:
        if after is not None:
            after.join(timeout=120)
        if not on_pi4():
            return
        peer = next((item for item in runtime.PEERS if item.get("name") == "pi4"), None)
        if not isinstance(peer, dict):
            return
        try:
            loaded = warm_residents(peer)
        except Exception as exc:
            print(f"model warm skipped: {type(exc).__name__}", flush=True)
            return
        print(f"model warm: {', '.join(loaded) or 'none'}", flush=True)

    thread = threading.Thread(target=run, name="model-warm", daemon=True)
    _WARM_THREAD = thread
    thread.start()
    return thread
