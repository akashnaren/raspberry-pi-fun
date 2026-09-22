"""Non-streaming chat against Ollama and llama.cpp."""
from __future__ import annotations

import json
import urllib.request


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def llamacpp_model(peer, model: str) -> str:
    models = peer.get("models") or []
    if models and (":" in model or model not in models):
        return models[0]
    return model


def chat_ollama(peer, model, messages, temperature=0.7, max_tokens=256):
    url = f"http://{peer['host']}:{peer['port']}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    out = _post_json(url, payload, timeout=180)
    text = (out.get("message") or {}).get("content") or out.get("response") or ""
    return text, model


def chat_llamacpp(peer, model, messages, temperature=0.7, max_tokens=256):
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
    content = (out.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return content, use
