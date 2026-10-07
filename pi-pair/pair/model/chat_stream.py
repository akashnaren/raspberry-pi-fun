"""Streaming chat. Ollama is NDJSON; llama.cpp is OpenAI SSE."""

from __future__ import annotations

import json
import time

from pair.core.cancel import ClientGone
from pair.model.chat_once import llamacpp_model, ollama_payload, open_json
from pair.mesh.guard import require_generative
from pair.model.knobs import inference_knobs
from pair.core.timing import from_ollama
from pair.model.think import (
    clip_reasoning,
    peel_think,
    reasoning_tokens,
    sample_knobs,
    stop_thinking,
)


def ollama_parts(line: str):
    """Return (content, thinking, done, skip, done_reason, usage).

    `thinking` is Ollama's separate channel. It is not answer text.
    `usage` is Ollama's eval counters on the done line, else None.
    """
    line = line.strip()
    if not line:
        return "", "", False, True, "", None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return "", "", False, True, "", None
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content") or ""
    thinking = message.get("thinking") or ""
    if not isinstance(content, str):
        content = ""
    if not isinstance(thinking, str):
        thinking = ""
    done = bool(obj.get("done"))
    reason = str(obj.get("done_reason") or "") if done else ""
    usage = from_ollama(obj) if done else None
    return content, thinking, done, False, reason, usage


def ollama_delta(line: str):
    """Return (text, done, skip). skip means the line was not a JSON object."""
    content, _thinking, done, skip, _reason, _usage = ollama_parts(line)
    return content, done, skip


def llamacpp_delta(line: str):
    """Return (text, done, skip) for one SSE line."""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return "", False, True
    data = line[5:].strip()
    if data == "[DONE]":
        return "", True, False
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return "", False, True
    choices = obj.get("choices") or []
    if not choices:
        return "", False, False
    delta = (choices[0].get("delta") or {}).get("content") or ""
    return delta, False, False


def ollama_event(line: str):
    """Return (text, done, skip, done_reason). One JSON parse."""
    raw = line.strip()
    if not raw:
        return "", False, True, ""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return "", False, True, ""
    chunk = (obj.get("message") or {}).get("content") or ""
    done = bool(obj.get("done"))
    reason = str(obj.get("done_reason") or "") if done else ""
    return chunk, done, False, reason


def llamacpp_event(line: str):
    """Return (text, done, skip, finish_reason). One JSON parse."""
    raw = line.strip()
    if not raw or not raw.startswith("data:"):
        return "", False, True, ""
    data = raw[5:].strip()
    if data == "[DONE]":
        return "", True, False, ""
    if not data:
        return "", False, True, ""
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return "", False, True, ""
    choices = obj.get("choices") or []
    if not choices:
        return "", False, False, ""
    choice = choices[0]
    delta = (choice.get("delta") or {}).get("content") or ""
    reason = str(choice.get("finish_reason") or "")
    return delta, bool(reason), False, reason


class TextStream:
    """Text deltas. `done_reason` is set while the body is read."""

    def __init__(self, producer):
        self.done_reason = ""
        self._producer = producer

    def __iter__(self):
        yield from self._producer(self)


def _read_ndjson(response, cancel=None):
    token = None
    if cancel is not None:
        abort = getattr(response, "abort", None)
        if abort is not None:
            token = cancel.attach(abort)
    try:
        while True:
            if cancel is not None:
                cancel.check()
            try:
                raw = response.readline()
            except OSError:
                if cancel is not None and cancel.gone():
                    raise ClientGone() from None
                raise
            if not raw:
                break
            yield raw.decode("utf-8", errors="replace")
    finally:
        if cancel is not None and token:
            cancel.detach(token)


def _keep_usage(sink: dict | None, usage: dict | None) -> None:
    if sink is None or not usage:
        return
    sink.clear()
    sink.update(usage)


def iter_ollama_channels(
    peer,
    model,
    messages,
    temperature=0.7,
    max_tokens=256,
    plan=None,
    cancel=None,
    usage: dict | None = None,
):
    """Yield ('thinking', text) or ('content', text) from one Ollama call.

    Levels pass think=false. A hand-built thinking plan still stops at the
    token cap or at about 25 seconds, and there is no second call. `<think>`
    tags are not an answer. An empty visible reply is left empty.
    """
    require_generative(peer)
    knobs = inference_knobs()
    think = bool(plan and plan.think)
    budget = int(plan.think_budget) if think and plan else 0
    seconds = float(plan.think_seconds) if think and plan else 0.0
    predict = int(plan.ollama_predict(max_tokens)) if plan else int(max_tokens)
    top_p, top_k, penalty = sample_knobs(plan)
    url = f"http://{peer['host']}:{peer['port']}/api/chat"
    payload = ollama_payload(
        model,
        messages,
        temperature,
        predict,
        True,
        knobs,
        think=think,
        top_p=top_p,
        top_k=top_k,
        presence_penalty=penalty,
    )
    accumulated = ""
    content_parts: list[str] = []
    saw_content = False
    capped = False
    started = time.monotonic()
    # The thinking call itself is bounded. After the first answer token the
    # socket may stay open for the rest of that reply.
    first_timeout = seconds if think and seconds else 180
    try:
        with open_json(url, payload, timeout=first_timeout, cancel=cancel) as response:
            for line in _read_ndjson(response, cancel):
                content, thinking, done, skip, reason, frame_usage = ollama_parts(line)
                if skip:
                    continue
                _keep_usage(usage, frame_usage)
                if done and usage is not None:
                    usage["done_reason"] = reason
                now = time.monotonic()
                if thinking and not capped:
                    merged = accumulated + thinking
                    if budget and stop_thinking(
                        reasoning_tokens(merged), started, now, budget, seconds
                    ):
                        kept = clip_reasoning(merged, budget)
                        piece = (
                            kept[len(accumulated) :]
                            if kept.startswith(accumulated)
                            else ""
                        )
                        if piece:
                            yield "thinking", piece
                        accumulated = kept
                        capped = True
                    else:
                        accumulated = merged
                        yield "thinking", thinking
                if (
                    not capped
                    and not saw_content
                    and budget
                    and stop_thinking(
                        reasoning_tokens(accumulated), started, now, budget, seconds
                    )
                ):
                    capped = True
                if content:
                    content_parts.append(content)
                    if peel_think("".join(content_parts))[0].strip():
                        saw_content = True
                    yield "content", content
                if done or (capped and not saw_content):
                    break
    except TimeoutError:
        return


def stream_ollama(
    peer,
    model,
    messages,
    temperature=0.7,
    max_tokens=256,
    plan=None,
    cancel=None,
    usage: dict | None = None,
):
    """Yield answer deltas from Ollama /api/chat with stream:true (NDJSON)."""

    def produce(stream: TextStream):
        del stream
        for kind, text in iter_ollama_channels(
            peer,
            model,
            messages,
            temperature,
            max_tokens,
            plan=plan,
            cancel=cancel,
            usage=usage,
        ):
            if kind == "content" and text:
                yield text

    return TextStream(produce)


def stream_llamacpp(
    peer, model, messages, temperature=0.7, max_tokens=256, cancel=None
):
    """Yield text deltas from llama.cpp OpenAI SSE /v1/chat/completions stream:true."""

    def produce(stream: TextStream):
        require_generative(peer)
        use = llamacpp_model(peer, model)
        url = f"http://{peer['host']}:{peer['port']}/v1/chat/completions"
        payload = {
            "model": use,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with open_json(url, payload, timeout=300, cancel=cancel) as response:
            for decoded in _read_ndjson(response, cancel):
                text, done, skip, reason = llamacpp_event(decoded)
                if skip:
                    continue
                if reason:
                    stream.done_reason = reason
                if text:
                    yield text
                if done:
                    break

    return TextStream(produce)
