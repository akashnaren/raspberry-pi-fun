"""Non-streaming chat against Ollama and llama.cpp."""

from __future__ import annotations

import json
import threading
import urllib.request

from pair.core import runtime
from pair.core.cancel import Cancel
from pair.core.config import on_pi4
from pair.core.timing import from_ollama
from pair.mesh.guard import may_generate, require_generative
from pair.model.http_pool import open_json_request
from pair.model.knobs import inference_knobs, keep_alive, ollama_options, tuned_knobs
from pair.model.modes import FLASH, PRO, mode_table
from pair.model.think import sample_knobs, split_ollama_message
from pair.turn.shape import warm_messages

_WARM_THREAD: threading.Thread | None = None
_WARM_MU = threading.Lock()
_WARM_CANCEL = Cancel()
# A warm holds _WARM_HELD for its one POST. A turn never does: it only waits
# until that warm drops, so two chats can overlap. Ollama still runs one
# request when NUM_PARALLEL is 1; closing the warm is what frees it.
_GUARD = threading.Condition()
_TURNS = 0
_WARM_HELD = False
_TURN_DEPTH = threading.local()


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
    if presence_penalty is not None:
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


def close_warm() -> None:
    """Close an in-flight warm so a user turn can use Ollama's one slot.

    Shares _GUARD with hold_warm_slot so the token that function returns is
    the one set here, not a fresh token installed in between.
    """
    global _WARM_CANCEL
    with _GUARD:
        with _WARM_MU:
            current = _WARM_CANCEL
            _WARM_CANCEL = Cancel()
    current.set()


def warm_token() -> Cancel:
    """The cancel token the current warm is, or will be, attached to."""
    with _WARM_MU:
        return _WARM_CANCEL


def turns_in_flight() -> int:
    """User turns inside begin_turn, including ones still waiting on a warm."""
    with _GUARD:
        return _TURNS


def warm_slot_busy() -> bool:
    """True while a warm is inside its model POST."""
    with _GUARD:
        return _WARM_HELD


def begin_turn() -> None:
    """Close an in-flight warm and wait until it drops. Do not hold the slot.

    Two chats both reach Ollama. A warm already in POST is best-effort: its
    socket is closed and this returns after that warm's finally runs.
    """
    global _TURNS
    depth = getattr(_TURN_DEPTH, "n", 0)
    if depth == 0:
        with _GUARD:
            _TURNS += 1
        try:
            close_warm()
            with _GUARD:
                while _WARM_HELD:
                    _GUARD.wait()
        except BaseException:
            with _GUARD:
                _TURNS -= 1
                _GUARD.notify_all()
            raise
    _TURN_DEPTH.n = depth + 1


def end_turn() -> None:
    global _TURNS
    depth = getattr(_TURN_DEPTH, "n", 0)
    if depth <= 0:
        return
    _TURN_DEPTH.n = depth - 1
    if depth == 1:
        with _GUARD:
            _TURNS = max(0, _TURNS - 1)
            _GUARD.notify_all()


def hold_warm_slot() -> Cancel | None:
    """Claim the slot and the token this warm must post with.

    None when a user turn is in flight or another warm holds the slot.
    The token is captured under the same lock as the claim, so a turn's
    close_warm cannot swap in a fresh token this warm would miss.
    """
    global _WARM_HELD
    with _GUARD:
        if _TURNS or _WARM_HELD:
            return None
        _WARM_HELD = True
        return warm_token()


def drop_warm_slot() -> None:
    global _WARM_HELD
    with _GUARD:
        _WARM_HELD = False
        _GUARD.notify_all()


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
        from pair.model.chat_stream import iter_ollama_channels

        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        taken: dict = {}
        for kind, text in iter_ollama_channels(
            peer,
            model,
            messages,
            temperature,
            max_tokens,
            plan=plan,
            cancel=cancel,
            usage=taken,
        ):
            if kind == "thinking" and text:
                thinking_parts.append(text)
            elif text:
                answer_parts.append(text)
        answer = "".join(answer_parts).strip()
        if meta is not None:
            meta["done_reason"] = "stop"
            meta["reasoning"] = "".join(thinking_parts).strip()
            meta["usage"] = taken
        return answer, model
    begin_turn()
    try:
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
            meta["usage"] = from_ollama(out)
        return answer, model
    finally:
        end_turn()


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
        from pair.model.chat_stream import stream_llamacpp

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


def warm_chat_model(
    peer: dict, model: str, timeout: float = 180, cancel: Cancel | None = None
) -> bool:
    """Load one tag with keep_alive -1. A user turn may close this request."""
    if not may_generate(peer) or not model:
        return False
    token = warm_token() if cancel is None else cancel
    if token.gone():
        return False
    held = hold_warm_slot()
    if held is None:
        return False
    try:
        if token.gone() or held.gone():
            return False
        knobs = tuned_knobs(model)
        url = f"http://{peer['host']}:{peer['port']}/api/chat"
        payload = ollama_payload(model, warm_messages(knobs), 0.0, 1, False, knobs)
        try:
            with open_json(url, payload, timeout, cancel=token) as response:
                response.read()
            return not token.gone()
        except Exception:
            return False
    finally:
        drop_warm_slot()


def warm_residents(peer: dict, timeout: float = 180) -> list[str]:
    """Load Flash, then Pro, when that tag is already on disk.

    A user turn closes the socket and the rest of this pass is skipped.
    """
    if not may_generate(peer):
        return []
    table = mode_table()
    loaded: list[str] = []
    cancel = warm_token()
    for name in (table[FLASH], table[PRO]):
        if not name or cancel.gone() or warm_slot_busy() or turns_in_flight():
            break
        if warm_chat_model(peer, name, timeout=timeout, cancel=cancel):
            loaded.append(name)
        if cancel.gone() or warm_slot_busy() or turns_in_flight():
            break
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
