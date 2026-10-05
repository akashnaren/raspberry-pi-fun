"""Streaming chat. Ollama is NDJSON; llama.cpp is OpenAI SSE."""

from __future__ import annotations

import json

from pair.chat import llamacpp_model, ollama_payload, open_json
from pair.guard import require_generative
from pair.knobs import inference_knobs


def ollama_delta(line: str):
    """Return (text, done, skip). skip means the line was not a JSON object."""
    line = line.strip()
    if not line:
        return "", False, True
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return "", False, True
    chunk = (obj.get("message") or {}).get("content") or ""
    return chunk, bool(obj.get("done")), False


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


def stream_ollama(peer, model, messages, temperature=0.7, max_tokens=256):
    """Yield text deltas from Ollama /api/chat with stream:true (NDJSON)."""

    def produce(stream: TextStream):
        require_generative(peer)
        knobs = inference_knobs()
        url = f"http://{peer['host']}:{peer['port']}/api/chat"
        payload = ollama_payload(model, messages, temperature, max_tokens, True, knobs)
        with open_json(url, payload, timeout=180) as response:
            while True:
                raw = response.readline()
                if not raw:
                    break
                text, done, skip, reason = ollama_event(
                    raw.decode("utf-8", errors="replace")
                )
                if skip:
                    continue
                if reason:
                    stream.done_reason = reason
                if text:
                    yield text
                if done:
                    break

    return TextStream(produce)


def stream_llamacpp(peer, model, messages, temperature=0.7, max_tokens=256):
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
        with open_json(url, payload, timeout=300) as response:
            while True:
                raw = response.readline()
                if not raw:
                    break
                text, done, skip, reason = llamacpp_event(
                    raw.decode("utf-8", errors="replace")
                )
                if skip:
                    continue
                if reason:
                    stream.done_reason = reason
                if text:
                    yield text
                if done:
                    break

    return TextStream(produce)
