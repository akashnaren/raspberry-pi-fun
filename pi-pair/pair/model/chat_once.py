"""Non-streaming chat against Ollama and llama.cpp."""

from __future__ import annotations

import json
import threading
import time
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
# Bumped once per begin_turn. A warm snapshots it around its POST so a turn
# that starts and ends during that POST still queues the tag again.
_TURN_EPOCH = 0
# Set when a chat gives up waiting for one stuck warm. Later turns skip the
# wait until that warm drops the slot.
_WARM_CLOSE_TIMED_OUT = False
# A chat waits this long for close_warm to drop the slot, then posts anyway.
WARM_CLOSE_WAIT_S = 2.0


class _Turn:
    """One begin_turn. end_turn clears it once, from any thread."""

    def __init__(self) -> None:
        self.live = False


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


def turn_epoch() -> int:
    """How many turns have begun. close_warm alone does not move this."""
    with _GUARD:
        return _TURN_EPOCH


def begin_turn() -> _Turn:
    """Close an in-flight warm and wait until it drops. Do not hold the slot.

    The returned token is what end_turn releases. A second end_turn on that
    same token does nothing, including when the stream is closed on another
    thread. Two chats both reach Ollama. A warm already in POST is
    best-effort: its socket is closed and this returns after that warm's
    finally runs, or after WARM_CLOSE_WAIT_S if the warm never drops.
    A warm that already timed out this wait does not cost the next turn
    another WARM_CLOSE_WAIT_S.
    """
    global _TURNS, _TURN_EPOCH
    token = _Turn()
    with _GUARD:
        _TURNS += 1
        _TURN_EPOCH += 1
        token.live = True
    try:
        close_warm()
        _wait_for_warm_close()
    except BaseException:
        end_turn(token)
        raise
    return token


def end_turn(token: _Turn | None) -> None:
    """Release `token`. A second call does not release some other turn."""
    global _TURNS
    if token is None:
        return
    with _GUARD:
        if not token.live:
            return
        token.live = False
        _TURNS -= 1
        _GUARD.notify_all()


def _wait_for_warm_close() -> None:
    """Return when the warm drops the slot, or when the wait bound passes.

    The first turn to hit the bound remembers it. Further turns do not wait
    again while that same warm still holds the slot.
    """
    global _WARM_CLOSE_TIMED_OUT
    with _GUARD:
        if not _WARM_HELD or _WARM_CLOSE_TIMED_OUT:
            return
        deadline = time.monotonic() + WARM_CLOSE_WAIT_S
        while _WARM_HELD:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _WARM_CLOSE_TIMED_OUT = True
                return
            _GUARD.wait(remaining)


def _decode_idle() -> bool:
    """True when no chat holds the decode slot or is waiting for it.

    Checked before _GUARD. The chat path takes the gate lock and later
    _GUARD; taking them in the other order can deadlock.
    """
    return runtime.gate.in_flight() == 0 and runtime.gate.waiting() == 0


def hold_warm_slot() -> Cancel | None:
    """Claim the slot and the token this warm must post with.

    None when a user turn is in flight, another warm holds the slot, or a
    chat holds the decode gate (including the wait before begin_turn).
    The token is captured under the same lock as the claim, so a turn's
    close_warm cannot swap in a fresh token this warm would miss.
    """
    global _WARM_HELD
    if not _decode_idle():
        return None
    with _GUARD:
        if _TURNS or _WARM_HELD:
            return None
        _WARM_HELD = True
        token = warm_token()
    # The gate is not taken under _GUARD. A chat can acquire it after the
    # idle check above and before this claim. Drop the slot if that happened.
    if _decode_idle():
        return token
    drop_warm_slot()
    return None


def drop_warm_slot() -> None:
    global _WARM_HELD, _WARM_CLOSE_TIMED_OUT
    with _GUARD:
        _WARM_HELD = False
        _WARM_CLOSE_TIMED_OUT = False
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
    turn = begin_turn()
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
        end_turn(turn)


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
    if cancel is not None and cancel.gone():
        return False
    held = hold_warm_slot()
    if held is None:
        return False
    try:
        if held.gone() or not _decode_idle():
            return False
        knobs = tuned_knobs(model)
        url = f"http://{peer['host']}:{peer['port']}/api/chat"
        payload = ollama_payload(model, warm_messages(knobs), 0.0, 1, False, knobs)
        if held.gone() or not _decode_idle():
            return False
        try:
            with open_json(url, payload, timeout, cancel=held) as response:
                response.read()
            return not held.gone()
        except Exception:
            return False
    finally:
        drop_warm_slot()


_RETRY_MU = threading.Lock()
_RETRY_MODELS: list[str] = []
_RETRY_THREAD: threading.Thread | None = None
_RETRY_STOP = threading.Event()
_RETRY_PEER: dict | None = None
_RETRY_TIMEOUT = 180.0


def join_startup_retry(timeout: float = 2.0) -> None:
    """Stop a background startup retry left behind by a test."""
    global _RETRY_THREAD
    thread = None
    with _RETRY_MU:
        _RETRY_STOP.set()
        _RETRY_MODELS.clear()
        thread = _RETRY_THREAD
    close_warm()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
    with _RETRY_MU:
        if _RETRY_THREAD is thread:
            _RETRY_THREAD = None
        _RETRY_STOP.clear()


def _queue_warm_retry(peer: dict, models: list[str], timeout: float) -> None:
    """Remember tags a chat cut off. One background thread drains the list."""
    global _RETRY_THREAD, _RETRY_PEER, _RETRY_TIMEOUT
    pending = [name for name in models if name]
    if not pending or _RETRY_STOP.is_set():
        return
    start = False
    with _RETRY_MU:
        if _RETRY_STOP.is_set():
            return
        for name in pending:
            if name not in _RETRY_MODELS:
                _RETRY_MODELS.append(name)
        _RETRY_PEER = peer
        _RETRY_TIMEOUT = timeout
        thread = _RETRY_THREAD
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(
            target=_retry_startup_warm, name="model-warm-retry", daemon=True
        )
        _RETRY_THREAD = thread
        start = True
    if start:
        thread.start()


def _warm_lane_free() -> bool:
    """True when a warm may take the slot: no turn, no warm, idle gate."""
    return not turns_in_flight() and not warm_slot_busy() and _decode_idle()


def _warm_interrupted(epoch: int) -> bool:
    """True when this POST was cut off by a turn or lost the lane.

    A 404 or a timeout leaves the epoch alone and the lane free, so it is
    not queued again. close_warm with no begin_turn does not move the epoch.
    """
    return (
        turns_in_flight() > 0
        or warm_slot_busy()
        or turn_epoch() != epoch
        or not _decode_idle()
    )


def _wait_for_warm_lane(timeout: float) -> bool:
    """True when a warm may post. False when this retry was stopped or timed out."""
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if _RETRY_STOP.is_set():
            return False
        if _warm_lane_free():
            return True
        time.sleep(0.02)
    return False


def _take_retry_batch() -> tuple[dict | None, list[str], float]:
    with _RETRY_MU:
        models = list(_RETRY_MODELS)
        peer = _RETRY_PEER if isinstance(_RETRY_PEER, dict) else None
        timeout = _RETRY_TIMEOUT
        _RETRY_MODELS.clear()
    return peer, models, timeout


def _retry_should_continue() -> bool:
    """True when more tags arrived. Otherwise this thread is done.

    Clearing the thread pointer and reading the queue share one lock, so a
    tag queued as this thread exits starts another thread instead of sitting
    on a dead one.
    """
    global _RETRY_THREAD
    with _RETRY_MU:
        if _RETRY_MODELS and not _RETRY_STOP.is_set():
            return True
        if _RETRY_STOP.is_set():
            _RETRY_MODELS.clear()
        if _RETRY_THREAD is threading.current_thread():
            _RETRY_THREAD = None
        return False


def _warm_retry_models(peer: dict, models: list[str], timeout: float) -> list[str]:
    """Warm each tag. Return the suffix a later turn cut off.

    A 404 or a timeout is skipped. Those are not queued again.
    """
    for index, name in enumerate(models):
        if _RETRY_STOP.is_set() or not _warm_lane_free():
            return models[index:]
        epoch = turn_epoch()
        if warm_chat_model(peer, name, timeout=timeout):
            continue
        if _RETRY_STOP.is_set() or _warm_interrupted(epoch):
            return models[index:]
    return []


def _retry_startup_warm() -> None:
    """Warm whatever the interrupted startup pass did not finish.

    Waits out the chat that closed the first pass. A later chat closes this
    one too, and the tags still missing stay queued.
    """
    while not _RETRY_STOP.is_set():
        peer, models, timeout = _take_retry_batch()
        if not models or peer is None:
            if not _retry_should_continue():
                return
            continue
        if not _wait_for_warm_lane(timeout):
            if _RETRY_STOP.is_set():
                _retry_should_continue()
                return
            _queue_warm_retry(peer, models, timeout)
            continue
        if not _warm_lane_free():
            _queue_warm_retry(peer, models, timeout)
            continue
        rest = _warm_retry_models(peer, models, timeout)
        if rest and not _RETRY_STOP.is_set():
            _queue_warm_retry(peer, rest, timeout)
            continue
        if not _retry_should_continue():
            return


def _warm_pass_blocked(cancel: Cancel) -> bool:
    return bool(cancel.gone() or not _warm_lane_free())


def warm_residents(peer: dict, timeout: float = 180) -> list[str]:
    """Load Flash, then Pro, when that tag is already on disk.

    A user turn closes the socket and the rest of this pass is skipped.
    Tags that pass did not load are warmed again after the turn finishes.
    A close with no user turn does not retry, and neither does a 404.
    """
    if not may_generate(peer):
        return []
    table = mode_table()
    names = [name for name in (table[FLASH], table[PRO]) if name]
    loaded: list[str] = []
    started = turn_epoch()
    cancel = warm_token()
    for index, name in enumerate(names):
        if _warm_pass_blocked(cancel):
            if _warm_interrupted(started):
                _queue_warm_retry(peer, names[index:], timeout)
            break
        epoch = turn_epoch()
        if warm_chat_model(peer, name, timeout=timeout, cancel=cancel):
            loaded.append(name)
            continue
        if _warm_interrupted(epoch):
            _queue_warm_retry(peer, names[index:], timeout)
        if _warm_pass_blocked(cancel):
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
