"""Streaming chat. Ollama is NDJSON; llama.cpp is OpenAI SSE."""
from __future__ import annotations

import json
import urllib.request

from pair.chat import llamacpp_model


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


def _open(url: str, payload: dict, timeout: float):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def stream_ollama(peer, model, messages, temperature=0.7, max_tokens=256):
    """Yield text deltas from Ollama /api/chat with stream:true (NDJSON)."""
    url = f"http://{peer['host']}:{peer['port']}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    with _open(url, payload, timeout=180) as response:
        while True:
            raw = response.readline()
            if not raw:
                break
            text, done, skip = ollama_delta(raw.decode("utf-8", errors="replace"))
            if skip:
                continue
            if text:
                yield text
            if done:
                break


def stream_llamacpp(peer, model, messages, temperature=0.7, max_tokens=256):
    """Yield text deltas from llama.cpp OpenAI SSE /v1/chat/completions stream:true."""
    use = llamacpp_model(peer, model)
    url = f"http://{peer['host']}:{peer['port']}/v1/chat/completions"
    payload = {
        "model": use,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    with _open(url, payload, timeout=300) as response:
        while True:
            raw = response.readline()
            if not raw:
                break
            text, done, skip = llamacpp_delta(raw.decode("utf-8", errors="replace"))
            if skip:
                continue
            if text:
                yield text
            if done:
                break
