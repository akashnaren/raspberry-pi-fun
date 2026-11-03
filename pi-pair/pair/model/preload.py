"""Keep Flash and Pro resident. This does not pull either tag.

Startup warms both. keep_alive stays the pi4 knob (-1 by default) on those
warms. If Ollama drops a tag, the same warm runs again. Pro waits while the
board is hot; Flash does not. A chat does not load a tag that /api/ps does
not already list, and a user Pro warm is not deferred.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from pair.core.cancel import ClientGone
from pair.core.config import ollama_base, on_pi4
from pair.model.chat_once import (
    close_warm,
    drop_warm_slot,
    hold_warm_slot,
    ollama_payload,
    open_json,
    turns_in_flight,
    warm_in_flight,
    warm_slot_busy,
)
from pair.model.http_pool import open_json_request
from pair.model.knobs import tuned_knobs
from pair.model.modes import PRO_MODEL, mode_table
from pair.model.rest import defer_nonurgent
from pair.turn.shape import warm_messages

PRELOAD_TIMEOUT_S = 180.0
COLD_WAIT_S = 60.0
COLD_POLL_S = 0.05
RESIDENT_TIMEOUT_S = 0.6
REWARM_PAUSE_S = 120.0
_SCHEDULED_MU = threading.Lock()
_SCHEDULED: list[threading.Thread] = []
_WARM_TAGS: set[str] = set()
_WARM_EPOCH = 0
# Cancelled POSTs per tag: (count, monotonic time the cooldown ends).
_WARM_STRIKES: dict[str, tuple[int, float]] = {}


def resident_tags() -> tuple[str, str]:
    """Flash and Pro tags that must stay loaded."""
    table = mode_table()
    flash = str(table.get("flash") or "").strip()
    pro = str(table.get("pro") or "").strip()
    return flash, pro


def pro_preload_payload(model: str | None = None) -> dict:
    """One-token /api/chat that leaves this tag resident. keep_alive is never 0."""
    tag = (model or "").strip()
    if not tag:
        _flash, tag = resident_tags()
    if not tag:
        tag = PRO_MODEL
    knobs = tuned_knobs(tag)
    payload = ollama_payload(tag, warm_messages(knobs), 0.0, 1, False, knobs)
    if payload.get("keep_alive") == 0:
        payload["keep_alive"] = -1
    return payload


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


def _warm_epoch() -> int:
    with _SCHEDULED_MU:
        return _WARM_EPOCH


def _turn_busy() -> bool:
    return (not _decode_idle()) or warm_slot_busy() or turns_in_flight() > 0


def _wait_out_the_turn(epoch: int) -> bool:
    """True when this warm should still post.

    Idle for the grace window means nobody took the model, so post. A chat
    that arrives during the grace runs first. A newer epoch means the warm
    was dropped and must not post.
    """
    grace = time.monotonic() + 0.5
    overlapped = False
    while time.monotonic() < grace:
        if _warm_epoch() != epoch:
            return False
        if _turn_busy():
            overlapped = True
            break
        time.sleep(0.01)
    if not overlapped:
        return _warm_epoch() == epoch
    while _turn_busy():
        if _warm_epoch() != epoch:
            return False
        time.sleep(0.02)
    return _warm_epoch() == epoch


def join_pro_warms(timeout: float = 2.0) -> None:
    """Stop scheduled Pro warms and wait until those threads leave."""
    global _WARM_EPOCH
    with _SCHEDULED_MU:
        _WARM_EPOCH += 1
        threads = [item for item in _SCHEDULED if item.is_alive()]
    close_warm()
    for thread in threads:
        thread.join(timeout)
    with _SCHEDULED_MU:
        _SCHEDULED[:] = [item for item in _SCHEDULED if item.is_alive()]
        _WARM_STRIKES.clear()


def schedule_pro_warm(host: str, port: int, model: str | None = None) -> None:
    """Background /api/chat warm for a user chat. Not deferred when the board is hot.

    The chat that scheduled it runs first. A later turn may close this request.
    One tag has one in-flight warm. PRELOAD_TIMEOUT_S bounds the POST only.
    Two cancelled POSTs cool the tag for REWARM_PAUSE_S. Later chats leave it
    alone; the watchdog loads Pro after that pause.
    """
    payload = pro_preload_payload(model)
    tag = str(payload.get("model") or "")
    url = f"http://{host}:{int(port)}/api/chat"

    count, _until = _strikes(tag)
    if count >= 2:
        return

    def run(epoch: int) -> None:
        try:
            _post_scheduled_warm(url, payload, epoch)
        finally:
            with _SCHEDULED_MU:
                _WARM_TAGS.discard(tag)

    with _SCHEDULED_MU:
        if tag in _WARM_TAGS:
            return
        _WARM_TAGS.add(tag)
        epoch = _WARM_EPOCH
        _SCHEDULED[:] = [item for item in _SCHEDULED if item.is_alive()]
        thread = threading.Thread(
            target=run, args=(epoch,), name="pro-rewarm", daemon=True
        )
        _SCHEDULED.append(thread)
    thread.start()


def _strikes(tag: str) -> tuple[int, float]:
    with _SCHEDULED_MU:
        return _WARM_STRIKES.get(tag, (0, 0.0))


def _record_cancel(tag: str) -> bool:
    """Count a cancelled POST. True once the tag should cool down.

    The count lives on the tag, so the next chat does not start a fresh thread
    at zero. Two strikes wait out REWARM_PAUSE_S, then the watchdog loads Pro.
    """
    with _SCHEDULED_MU:
        count, _until = _WARM_STRIKES.get(tag, (0, 0.0))
        count += 1
        until = time.monotonic() + REWARM_PAUSE_S if count >= 2 else 0.0
        _WARM_STRIKES[tag] = (count, until)
        return count >= 2


def _clear_strikes(tag: str) -> None:
    with _SCHEDULED_MU:
        _WARM_STRIKES.pop(tag, None)


def _post_scheduled_warm(url: str, payload: dict, epoch: int) -> None:
    """Wait until the lane is idle, then POST. A lost hold waits again.

    Two cancelled POSTs cool the tag for REWARM_PAUSE_S. Later chats leave
    the reload alone. The watchdog loads Pro after that pause.
    """
    tag = str(payload.get("model") or "")
    while _warm_epoch() == epoch:
        if not _wait_out_the_turn(epoch):
            return
        token = hold_warm_slot()
        if token is None:
            if _warm_epoch() != epoch:
                return
            time.sleep(0.02)
            continue
        try:
            if _warm_epoch() != epoch:
                return
            # A chat closed this hold. The tag is still in flight, so wait
            # for the lane and post again instead of ending the warm.
            if token.gone() or not _decode_idle():
                continue
            try:
                with open_json(
                    url, payload, PRELOAD_TIMEOUT_S, cancel=token
                ) as response:
                    response.read()
            except ClientGone:
                if not _record_cancel(tag):
                    continue
                print(f"pro preload skipped: {tag} interrupted", flush=True)
                return
            except Exception as exc:
                print(f"pro preload skipped: {exc}", flush=True)
                return
            _clear_strikes(tag)
            return
        finally:
            drop_warm_slot()


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
    """Reload a tag /api/ps dropped. Pro waits while rest says the board is hot.

    A tag cooled by two cancelled POSTs also waits out REWARM_PAUSE_S. When
    that pause ends, this watchdog loads Pro. A chat schedule does not.
    """
    if not on_pi4():
        return
    if not _decode_idle():
        return
    host, port = _local_endpoint()
    resident = resident_models(host, port)
    if resident is None:
        return
    flash, pro = resident_tags()
    if flash and flash in resident:
        _clear_strikes(flash)
    elif flash and flash not in resident:
        warm_model(flash)
    if pro and pro in resident:
        _clear_strikes(pro)
    elif pro and pro not in resident:
        if defer_nonurgent():
            print("rest: deferred pro rewarm", flush=True)
            return
        count, until = _strikes(pro)
        if count >= 2 and time.monotonic() < until:
            return
        warm_model(pro)


def _retry_cancelled_warm(model: str) -> None:
    """Log a warm a chat closed and post it again without the 120s pause.

    Pro waits while the board is hot, the same way the eviction watchdog does.
    """
    print(f"model preload skipped: {model} cancelled", flush=True)
    _flash, pro = resident_tags()
    if model == pro and defer_nonurgent():
        print("rest: deferred pro rewarm", flush=True)
        return
    host, port = _local_endpoint()
    schedule_pro_warm(host, port, model)


def warm_model(model: str | None = None) -> None:
    """POST /api/chat for one resident tag. No-op off the brain."""
    if not on_pi4():
        return
    payload = pro_preload_payload(model)
    if not payload.get("model") or payload.get("keep_alive") == 0:
        return
    name = str(payload.get("model") or "")
    if warm_slot_busy():
        print(f"model preload skipped: {name} busy", flush=True)
        return
    token = hold_warm_slot()
    if token is None:
        print(f"model preload skipped: {name} busy", flush=True)
        return
    request = urllib.request.Request(
        ollama_base().rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        if token.gone() or not _decode_idle():
            _retry_cancelled_warm(name)
            return
        try:
            with open_json_request(
                request, PRELOAD_TIMEOUT_S, cancel=token
            ) as response:
                response.read()
        except ClientGone:
            _retry_cancelled_warm(name)
            return
        except Exception as exc:
            print(f"model preload skipped: {exc}", flush=True)
            return
        if not token.gone():
            print(f"model preload: {payload['model']}", flush=True)
            _clear_strikes(name)
    finally:
        drop_warm_slot()


def warm_pro_model() -> None:
    """POST /api/chat for Pro. No-op off the brain. Failures are logged."""
    _flash, pro = resident_tags()
    warm_model(pro)


def start_pro_warm() -> threading.Thread:
    """Daemon preload so listen is not blocked on either tag.

    The first eviction check waits until the startup warm finishes, so boot
    does not load Flash or Pro a second time.
    """

    def run() -> None:
        pending = warm_in_flight()
        if pending is not None:
            pending.join()
        while True:
            try:
                rewarm_pro_if_evicted()
            except Exception as exc:
                print(f"pro preload skipped: {exc}", flush=True)
            time.sleep(REWARM_PAUSE_S)

    thread = threading.Thread(target=run, name="pro-preload", daemon=True)
    thread.start()
    return thread
