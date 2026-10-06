"""Non-streaming chat against Ollama and llama.cpp."""

from __future__ import annotations

import json
import threading
import urllib.request

from pair import runtime
from pair.config import on_pi4
from pair.guard import may_generate, require_generative
from pair.http_pool import open_json_request
from pair.knobs import inference_knobs, keep_alive, ollama_options
from pair.modes import FLASH, PRO, mode_table
from pair.think import sample_knobs, split_ollama_message

_WARM_THREAD: threading.Thread | None = None


def open_json(url: str, payload: dict, timeout: float, cancel=None):
    """POST JSON. Chat, stream, and the startup warm share this opener."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    return open_json_request(request, timeout, cancel=cancel)


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    with open_json(url, payload, timeout) as response:
        return json.loads(response.read().decode())


def ollama_payload(
    model,
    messages,
    temperature,
    max_tokens,
    stream: bool,
    knobs=None,
    think: bool = False,
    top_p: float | None = None,
    top_k: int | None = None,
    presence_penalty: float | None = None,
) -> dict:
    """The one Ollama chat body. keep_alive is the pi4 knob, not a per-call TTL.

    `think` is Ollama's native switch. False is a direct answer. Sampling
    follows the Qwen3 card. A missing plan uses the non-thinking sample.
    """
    row = inference_knobs() if knobs is None else knobs
    options = ollama_options(temperature, max_tokens, row, model)
    if top_p is not None:
        options["top_p"] = float(top_p)
    if top_k is not None:
        options["top_k"] = int(top_k)
    if presence_penalty:
        options["presence_penalty"] = float(presence_penalty)
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": keep_alive(row),
        "think": bool(think),
        "options": options,
    }


def llamacpp_model(peer, model: str) -> str:
    models = peer.get("models") or []
    if models and (":" in model or model not in models):
        return models[0]
    return model


def chat_ollama(
    peer,
    model,
    messages,
    temperature=0.7,
    max_tokens=256,
    meta: dict | None = None,
    plan=None,
    cancel=None,
):
    require_generative(peer)
    think = bool(plan and plan.think)
    if think or cancel is not None:
        from pair.stream import iter_ollama_channels

        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        for kind, text in iter_ollama_channels(
            peer,
            model,
            messages,
            temperature,
            max_tokens,
            plan=plan,
            cancel=cancel,
        ):
            if kind == "thinking" and text:
                thinking_parts.append(text)
            elif text:
                answer_parts.append(text)
        answer = "".join(answer_parts).strip()
        if meta is not None:
            meta["done_reason"] = "stop"
            meta["reasoning"] = "".join(thinking_parts).strip()
        return answer, model
    knobs = inference_knobs()
    url = f"http://{peer['host']}:{peer['port']}/api/chat"
    top_p, top_k, penalty = sample_knobs(plan)
    predict = int(plan.ollama_predict(max_tokens)) if plan else int(max_tokens)
    payload = ollama_payload(
        model,
        messages,
        temperature,
        predict,
        False,
        knobs,
        think=False,
        top_p=top_p,
        top_k=top_k,
        presence_penalty=penalty,
    )
    out = _post_json(url, payload, timeout=180)
    answer, thinking = split_ollama_message(out.get("message") or {})
    if meta is not None:
        meta["done_reason"] = str(out.get("done_reason") or "")
        meta["reasoning"] = thinking
    return answer, model


def chat_llamacpp(
    peer,
    model,
    messages,
    temperature=0.7,
    max_tokens=256,
    meta: dict | None = None,
    cancel=None,
):
    require_generative(peer)
    use = llamacpp_model(peer, model)
    if cancel is not None:
        from pair.stream import stream_llamacpp

        parts: list[str] = []
        produced = stream_llamacpp(
            peer, model, messages, temperature, max_tokens, cancel=cancel
        )
        for text in produced:
            if text:
                parts.append(text)
        if meta is not None:
            meta["done_reason"] = produced.done_reason or "stop"
        return "".join(parts), use
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
    """Load Flash, then Pro if it is already on disk.

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
    return loaded


def warm_in_flight() -> threading.Thread | None:
    """The startup warm, if it has not finished. Request search can overlap it."""
    thread = _WARM_THREAD
    if thread is not None and thread.is_alive():
        return thread
    return None


def start_model_warm() -> threading.Thread:
    """Load Flash, then Pro if that tag is already on disk.

    The thread does not block accept. A failure is logged and ignored.
    """
    global _WARM_THREAD

    def run() -> None:
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
