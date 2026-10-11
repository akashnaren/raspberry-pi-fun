#!/usr/bin/env python3
"""Live proof and the Step 0 tool-call probe.

The probe posts straight to Ollama on this machine. It does not restart
anything. Run it on pi4 while /health is idle:

    python3 pi-pair/scripts/qa/search_decide_live.py --probe
    python3 pi-pair/scripts/qa/search_decide_live.py --probe --probe-variant b

The live proof runs from another board against the router. It does not
cache answers. Logs go to --log-dir when that directory exists.

    python3 pi-pair/scripts/qa/search_decide_live.py \
        --base http://10.0.0.166:18080 --tunnel https://<brain-tunnel> \
        --reps 3 --baseline-in r104/post_rollback_baseline.json \
        --log-dir /workspace/openpi-qa/r105
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NATAN = "Who is Natan Weitzman from Tesla - use your tools web search"
NEWS = (
    "What is the latest news?",
    "NASA latest",
)
FAST = (
    "hi",
    "What is 2+2?",
    "Write a Python function that reverses a string.",
    "Write a four-line poem about rain.",
)
# Post-rollback medians from the #104 proof. --baseline-in overrides these.
POST_ROLLBACK_MS = {
    "hi": 2132,
    "What is 2+2?": 711,
    "Write a Python function that reverses a string.": 3110,
    "Write a four-line poem about rain.": 7403,
}
PRIMES = "List every prime number from 1 to 50."
CITIES = "What are the top 10 cities to visit in Europe?"
ORDINARY = (
    "how to follow people on X",
    "bath bombs recipe",
    "Stalker 1979 film",
    "track my package",
)
HORROR = "top 5 horror movies"
FLASH = "qwen3:0.6b"
PROBE_SEARCH = (NATAN, "What is the latest news?", "NASA latest")
_GARBLE = ("```", "<search>", "/no_think", "None of the provided notes", "<tool_call>")
_TESLA = re.compile(r"(?:former\s+)?(?:ceo|founder|chairman)\s+of\s+tesla", re.I)
_ITEM = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+\S")
_VARIANT_B = " Do not use it for greetings, math, code, or creative writing."


class Log:
    def __init__(self, directory: str | None):
        self._handle = None
        if not directory:
            return
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        self._handle = (path / "proof.jsonl").open("a", encoding="utf-8")

    def write(self, row: dict) -> None:
        line = json.dumps(row, ensure_ascii=False)
        print(line, flush=True)
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()


def chat_body(
    prompt: str,
    web_search: bool | None = None,
    mode: str = "flash",
    stream: bool = False,
) -> dict:
    body = {
        "model": FLASH,
        "mode": mode,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream,
        "pi_target": "auto",
        "pi_mesh": "on",
    }
    if web_search is not None:
        body["web_search"] = web_search
    return body


def post_json(url: str, payload: dict | None, timeout: float) -> tuple[int, dict, str]:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"content-type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
            peer = response.headers.get("X-Pi-Peer") or ""
            parsed = json.loads(raw or "{}")
            return response.status, parsed if isinstance(parsed, dict) else {}, peer
    except urllib.error.HTTPError as error:
        raw = error.read().decode()
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            parsed = {"error": raw[:200]}
        peer = error.headers.get("X-Pi-Peer") or ""
        return error.code, parsed if isinstance(parsed, dict) else {}, peer


def reply_text(body: dict) -> str:
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "")


def _timing(body: dict, key: str) -> int:
    timing = body.get("pi_timing") if isinstance(body.get("pi_timing"), dict) else {}
    try:
        return int(timing.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _median(values: list[int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _garble(text: str) -> str:
    for token in _GARBLE:
        if token in (text or ""):
            return token
    return ""


def _sources(body: dict) -> list:
    sources = body.get("pi_sources")
    return sources if isinstance(sources, list) else []


def _invented_tesla(reply: str, sources: list) -> bool:
    if _TESLA.search(reply or "") is None:
        return False
    return _TESLA.search(json.dumps(sources, ensure_ascii=False)) is None


def _list_items(text: str) -> int:
    return len(_ITEM.findall(text or ""))


def _primes_problem(text: str) -> str:
    cleaned = re.sub(r"\b1\s*(?:to|-|–|—)\s*50\b", " ", text or "", flags=re.I)
    numbers = {int(token) for token in re.findall(r"\b\d+\b", cleaned)}
    wanted = [
        number
        for number in range(2, 48)
        if all(number % divisor for divisor in range(2, int(number**0.5) + 1))
    ]
    missing = [number for number in wanted if number not in numbers]
    extras = sorted(
        number for number in numbers if 1 < number < 50 and number not in wanted
    )
    if missing:
        return f"missing primes {missing}"
    if extras:
        return f"extra numbers {extras}"
    return ""


def _peer_ok(failures: list[str], body: dict, peer: str, prompt: str) -> None:
    if body.get("pi_peer") != "pi4":
        failures.append(f"{prompt} pi_peer {body.get('pi_peer')!r}")
    if peer != "pi4":
        failures.append(f"{prompt} X-Pi-Peer {peer!r}")


def _print_turn(log: Log, prompt: str, body: dict, peer: str) -> None:
    text = reply_text(body).replace("\n", " ")
    log.write(
        {
            "prompt": prompt,
            "pi_stages": body.get("pi_stages"),
            "pi_decide": body.get("pi_decide"),
            "pi_decide_source": body.get("pi_decide_source"),
            "pi_search_query": body.get("pi_search_query"),
            "pi_search": body.get("pi_search"),
            "pi_sources": len(_sources(body)),
            "tool_rounds": _timing(body, "tool_rounds"),
            "prefill_tokens": _timing(body, "prefill_tokens"),
            "total_ms": _timing(body, "total_ms"),
            "pi_peer": body.get("pi_peer"),
            "X-Pi-Peer": peer,
            "reply": text[:160],
        }
    )


def _cool(root: str, timeout: float, log: Log) -> str:
    """Wait while pi4 is above 80 C. Empty when it is cool or has no reading."""
    for _attempt in range(80):
        try:
            status, body, _peer = post_json(f"{root}/health", None, min(timeout, 15))
        except Exception as error:
            return f"health while cooling: {type(error).__name__}: {error}"
        if status != 200:
            return f"health while cooling returned {status}"
        temp = body.get("temp_c")
        try:
            hot = temp is not None and float(temp) > 80
        except (TypeError, ValueError):
            return ""
        if not hot:
            return ""
        log.write({"pause": "hot", "temp_c": temp})
        time.sleep(15)
    return "pi4 stayed above 80 C"


def _one(failures, log, root, prompt, timeout, web_search, mode="flash"):
    hot = _cool(root, timeout, log)
    if hot:
        failures.append(hot)
        return None, ""
    label = prompt if web_search is None else f"{prompt} web_search"
    try:
        status, body, peer = post_json(
            f"{root}/v1/chat/completions",
            chat_body(prompt, web_search, mode=mode),
            timeout,
        )
    except Exception as error:
        failures.append(f"{label}: {type(error).__name__}: {error}")
        return None, ""
    _print_turn(log, label, body, peer)
    if status != 200:
        failures.append(f"{label} status {status}")
        return None, peer
    return body, peer


def _clean_reply(failures, prompt, body) -> None:
    text = reply_text(body)
    if not text.strip():
        failures.append(f"{prompt} empty reply")
    token = _garble(text)
    if token:
        failures.append(f"{prompt} garble {token!r}")


def _search_turn(failures, log, root, prompt, timeout, *, claim=False, min_chars=1):
    body, peer = _one(failures, log, root, prompt, timeout, None)
    if body is None:
        return
    stages = body.get("pi_stages") or []
    if "searching" not in stages:
        failures.append(f"expected a search: {prompt}")
    if body.get("pi_decide") != "search":
        failures.append(f"{prompt} decision {body.get('pi_decide')!r}")
    if body.get("pi_decide_source") != "model":
        failures.append(f"{prompt} source {body.get('pi_decide_source')!r}")
    if claim and not str(body.get("pi_search_query") or "").strip():
        failures.append(f"{prompt} empty pi_search_query")
    text = reply_text(body)
    if len(text.strip()) < min_chars:
        failures.append(f"{prompt} reply length {len(text.strip())}")
    token = _garble(text)
    if token:
        failures.append(f"{prompt} garble {token!r}")
    if claim and _invented_tesla(text, _sources(body)):
        failures.append(f"{prompt} invented a Tesla title")
    _peer_ok(failures, body, peer, prompt)


def _fast_turn(failures, samples, log, root, prompt, timeout) -> None:
    body, peer = _one(failures, log, root, prompt, timeout, None)
    if body is None:
        return
    stages = body.get("pi_stages") or []
    if "searching" in stages:
        failures.append(f"fast prompt searched: {prompt}")
    if body.get("pi_decide") != "skip":
        failures.append(f"{prompt} decision {body.get('pi_decide')!r}")
    samples[prompt]["total_ms"].append(_timing(body, "total_ms"))
    samples[prompt]["prefill_tokens"].append(_timing(body, "prefill_tokens"))
    _peer_ok(failures, body, peer, prompt)


def _toggle(failures, log, root, timeout) -> None:
    forced, peer = _one(failures, log, root, "hi", timeout, True)
    if forced is None:
        return
    stages = forced.get("pi_stages") or []
    if "searching" not in stages:
        failures.append("toggle on did not search for hi")
    if forced.get("pi_decide_source") != "user":
        failures.append(f"toggle source was {forced.get('pi_decide_source')!r}")
    if forced.get("pi_decide") != "search":
        failures.append(f"toggle decision was {forced.get('pi_decide')!r}")
    _clean_reply(failures, "hi web_search", forced)
    _peer_ok(failures, forced, peer, "hi web_search")


def _bad_flag(failures, log, root, timeout) -> None:
    payload = chat_body("hi")
    payload["web_search"] = "yes"
    try:
        status, body, _peer = post_json(f"{root}/v1/chat/completions", payload, timeout)
    except Exception as error:
        failures.append(f"web_search yes: {type(error).__name__}: {error}")
        return
    log.write(
        {"prompt": "web_search yes", "status": status, "error": body.get("error")}
    )
    if status != 400 or body.get("error") != "web_search must be true or false.":
        failures.append(f"web_search yes status {status} body {body.get('error')!r}")


def _ordinary(failures, log, root, prompt, timeout, mode="flash") -> None:
    body, peer = _one(failures, log, root, prompt, timeout, None, mode=mode)
    if body is None:
        return
    _clean_reply(failures, prompt, body)
    _peer_ok(failures, body, peer, prompt)


def _counted(failures, log, root, prompt, timeout, need: int, mode: str) -> None:
    body, peer = _one(failures, log, root, prompt, timeout, None, mode=mode)
    if body is None:
        return
    _clean_reply(failures, prompt, body)
    _peer_ok(failures, body, peer, prompt)
    found = _list_items(reply_text(body))
    if found < need:
        failures.append(f"{prompt} list items {found} < {need}")


def _primes(failures, log, root, timeout) -> None:
    body, peer = _one(failures, log, root, PRIMES, timeout, None, mode="flash")
    if body is None:
        return
    _clean_reply(failures, PRIMES, body)
    _peer_ok(failures, body, peer, PRIMES)
    problem = _primes_problem(reply_text(body))
    if problem:
        failures.append(f"{PRIMES} {problem}")


def _latency(failures, samples, bounds) -> dict:
    medians = {}
    for prompt, rows in samples.items():
        total = _median(rows["total_ms"])
        medians[prompt] = {
            "total_ms": total,
            "prefill_tokens": _median(rows["prefill_tokens"]),
        }
        if not rows["total_ms"]:
            failures.append(f"{prompt} has no timing samples")
            continue
        base = float(bounds[prompt])
        limit = max(base + 500, base * 1.2)
        if total > limit:
            failures.append(f"{prompt} median total_ms {total} > {limit} (base {base})")
    return medians


def _warm_prefill(failures, samples) -> None:
    values = samples["hi"]["prefill_tokens"]
    if not values:
        failures.append("warm hi has no prefill sample")
        return
    warm = values[-1]
    if warm <= 0 or warm > 60:
        failures.append(f"warm hi prefill_tokens {warm}")


def _health(failures, log, root, tunnel, timeout) -> None:
    try:
        status, body, _peer = post_json(f"{root}/health", None, timeout)
    except Exception as error:
        failures.append(f"health: {type(error).__name__}: {error}")
        return
    log.write({"health_status": status, "peers_up": body.get("peers_up")})
    if status != 200 or body.get("peers_up") != 3:
        failures.append(f"peers_up {body.get('peers_up')!r}")
    services = body.get("services") if isinstance(body.get("services"), dict) else {}
    search = services.get("search") if isinstance(services.get("search"), dict) else {}
    if not search.get("ok"):
        failures.append("services.search.ok is false")
    try:
        code, _payload, _header = post_json(
            f"{tunnel.rstrip('/')}/v1/search", {"q": "hi"}, timeout
        )
    except Exception as error:
        failures.append(f"tunnel search: {type(error).__name__}: {error}")
        return
    log.write({"tunnel_search": code})
    if code != 403:
        failures.append(f"tunnel /v1/search returned {code}")


def _ps_models(host: str, timeout: float) -> list | None:
    url = f"http://{host}:11434/api/ps"
    try:
        status, body, _peer = post_json(url, None, min(timeout, 10))
    except Exception:
        return None
    if status != 200:
        return None
    models = body.get("models")
    return models if isinstance(models, list) else None


def _ps_check(failures, log, before: list | None, after: list | None) -> None:
    if before is None or after is None:
        log.write({"api_ps": "skipped"})
        return
    log.write({"api_ps_before": before, "api_ps_after": after})
    names = {str(row.get("name") or row.get("model") or "") for row in after}
    for tag in (FLASH, "qwen3:1.7b"):
        if not any(tag in name for name in names):
            failures.append(f"/api/ps missing {tag}")
    by_name = {
        str(row.get("name") or row.get("model") or ""): row
        for row in before
        if isinstance(row, dict)
    }
    for row in after:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("model") or "")
        ctx = row.get("context_length")
        if ctx is not None and int(ctx) != 2048:
            failures.append(f"{name} context_length {ctx}")
        prior = by_name.get(name) or {}
        if prior.get("expires_at") and prior.get("expires_at") != row.get("expires_at"):
            failures.append(f"{name} expires_at changed")


def _load_bounds(path: str | None, failures: list[str]) -> dict:
    bounds = dict(POST_ROLLBACK_MS)
    if not path:
        return bounds
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"baseline: {error}")
        return bounds
    rows = saved.get("prompts") if isinstance(saved, dict) else None
    if not isinstance(rows, dict):
        failures.append("baseline file has no prompts")
        return bounds
    for prompt in FAST:
        previous = rows.get(prompt) or {}
        try:
            bounds[prompt] = float(previous.get("total_ms"))
        except (TypeError, ValueError, AttributeError):
            failures.append(f"baseline missing total_ms for {prompt}")
    return bounds


def run(base, tunnel, reps, timeout, bounds, log, host) -> tuple[list[str], dict]:
    failures: list[str] = []
    samples = {prompt: {"total_ms": [], "prefill_tokens": []} for prompt in FAST}
    root = base.rstrip("/")
    before = _ps_models(host, timeout)
    for _rep in range(reps):
        _search_turn(failures, log, root, NATAN, timeout, claim=True, min_chars=40)
        for prompt in NEWS:
            _search_turn(failures, log, root, prompt, timeout)
        for prompt in FAST:
            _fast_turn(failures, samples, log, root, prompt, timeout)
        _toggle(failures, log, root, timeout)
        _bad_flag(failures, log, root, timeout)
        _primes(failures, log, root, timeout)
        _counted(failures, log, root, CITIES, timeout, 10, "auto")
        for prompt in ORDINARY:
            _ordinary(failures, log, root, prompt, timeout)
        _counted(failures, log, root, HORROR, timeout, 5, "flash")
    medians = _latency(failures, samples, bounds)
    _warm_prefill(failures, samples)
    _health(failures, log, root, tunnel, timeout)
    _ps_check(failures, log, before, _ps_models(host, timeout))
    _stream_once(failures, log, root, timeout)
    return failures, medians


def _stream_once(failures, log, root, timeout) -> None:
    hot = _cool(root, timeout, log)
    if hot:
        failures.append(hot)
        return
    payload = chat_body(NATAN, stream=True)
    request = urllib.request.Request(
        f"{root}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except Exception as error:
        failures.append(f"stream {NATAN}: {type(error).__name__}: {error}")
        return
    with response:
        peer = response.headers.get("X-Pi-Peer") or ""
        if response.status != 200:
            failures.append(f"stream status {response.status}")
        text, final, leaked = _read_sse(response)
    log.write(
        {
            "prompt": "stream " + NATAN,
            "pi_stages": final.get("pi_stages"),
            "pi_decide": final.get("pi_decide"),
            "pi_decide_source": final.get("pi_decide_source"),
            "reply": text.replace("\n", " ")[:160],
            "X-Pi-Peer": peer,
        }
    )
    stages = final.get("pi_stages") or []
    if "searching" not in stages:
        failures.append("stream Natan did not search")
    if final.get("pi_decide") != "search" or final.get("pi_decide_source") != "model":
        failures.append(
            f"stream Natan decide {final.get('pi_decide')!r} "
            f"{final.get('pi_decide_source')!r}"
        )
    if len(text.strip()) < 40:
        failures.append(f"stream Natan reply length {len(text.strip())}")
    token = _garble(text)
    if token:
        failures.append(f"stream Natan garble {token!r}")
    if leaked:
        failures.append("stream leaked tool JSON")
    if peer != "pi4":
        failures.append(f"stream X-Pi-Peer {peer!r}")


def _read_sse(response) -> tuple[str, dict, bool]:
    parts: list[str] = []
    final: dict = {}
    leaked = False
    pending = ""
    while True:
        raw = response.read(4096)
        if not raw:
            break
        pending += raw.decode(errors="replace")
        while "\n\n" in pending:
            block, pending = pending.split("\n\n", 1)
            piece, row, leak = _sse_block(block)
            if leak:
                leaked = True
            if piece:
                parts.append(piece)
            if row:
                final = row
    return "".join(parts), final, leaked


def _sse_block(block: str) -> tuple[str, dict, bool]:
    data = ""
    for line in block.splitlines():
        if line.startswith("data:"):
            data += line[5:].strip()
    if not data or data == "[DONE]":
        return "", {}, False
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return "", {}, False
    if not isinstance(obj, dict):
        return "", {}, False
    choices = obj.get("choices") or []
    delta = {}
    if choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta") or choices[0].get("message") or {}
    text = str(delta.get("content") or "") if isinstance(delta, dict) else ""
    leaked = "<tool_call>" in text or '{"name"' in text
    summary = obj if obj.get("pi_decide") or obj.get("pi_stages") else {}
    return text, summary, leaked


def _pair_imports():
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pair.model.chat_once import ollama_payload
    from pair.model.knobs import inference_knobs
    from pair.model.modes import mode_table
    from pair.model.think import sample_knobs
    from pair.turn.search_tool import SEARCH_TOOL, tool_query
    from pair.turn.shape import shape_messages

    return (
        ollama_payload,
        inference_knobs,
        mode_table,
        sample_knobs,
        SEARCH_TOOL,
        tool_query,
        shape_messages,
    )


def _command(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _preflight(failures: list[str], log: Log, models: list[str], ollama: str) -> None:
    try:
        status, body, _peer = post_json(f"{ollama.rstrip('/')}/api/version", None, 10)
    except Exception as error:
        failures.append(f"ollama version: {type(error).__name__}: {error}")
        return
    log.write({"ollama_version": body, "status": status})
    if status != 200:
        failures.append(f"ollama /api/version status {status}")
    parallel = os.environ.get("OLLAMA_NUM_PARALLEL") or "(unset in this process)"
    log.write({"OLLAMA_NUM_PARALLEL": parallel})
    for model in models:
        code, shown = _command(["ollama", "show", model])
        if code != 0 or "tools" not in shown.lower():
            failures.append(f"{model} capabilities do not list tools")
        code, template = _command(["ollama", "show", model, "--template"])
        if code != 0 or "/no_think" not in template:
            failures.append(f"{model} template has no /no_think")


def _idle(health_url: str) -> str:
    try:
        status, body, _peer = post_json(health_url, None, 5)
    except Exception as error:
        return f"note: pi-pair health unreachable ({type(error).__name__}: {error})"
    if status != 200:
        return f"note: pi-pair health status {status}"
    in_flight = int(body.get("in_flight") or 0)
    waiting = int(body.get("waiting") or 0)
    if in_flight or waiting:
        return f"refuse: in_flight={in_flight} waiting={waiting}"
    return ""


def _tool_for(variant: str, search_tool: dict) -> dict:
    if variant != "b":
        return search_tool
    cloned = json.loads(json.dumps(search_tool))
    cloned["function"]["description"] += _VARIANT_B
    return cloned


def _ollama_chat(
    ollama: str, payload: dict, timeout: float
) -> tuple[str, list | None, int]:
    request = urllib.request.Request(
        f"{ollama.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    content: list[str] = []
    calls = None
    prompt_eval = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line:
                continue
            obj = json.loads(line)
            message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            piece = message.get("content") or ""
            if piece:
                content.append(str(piece))
            if isinstance(message.get("tool_calls"), list):
                calls = message["tool_calls"]
            if obj.get("done"):
                try:
                    prompt_eval = int(obj.get("prompt_eval_count") or 0)
                except (TypeError, ValueError):
                    prompt_eval = 0
    return "".join(content), calls, prompt_eval


def _probe_prompt(log, ollama, payload_fn, tool_query, model, prompt, tool, timeout):
    messages = payload_fn["shape"](
        [{"role": "user", "content": prompt}], prompt, payload_fn["knobs"]
    )
    top_p, top_k, penalty = payload_fn["sample"](None)
    payload = payload_fn["ollama"](
        model,
        messages,
        0.7,
        256,
        True,
        payload_fn["knobs"],
        think=False,
        top_p=top_p,
        top_k=top_k,
        presence_penalty=penalty,
        tools=[tool],
    )
    content, calls, prompt_eval = _ollama_chat(ollama, payload, timeout)
    message = {"tool_calls": calls} if calls else {}
    found = tool_query(message, content, prompt)
    log.write(
        {
            "probe": True,
            "model": model,
            "prompt": prompt,
            "tool": found is not None,
            "query": found,
            "prompt_eval_count": prompt_eval,
            "reply": content.replace("\n", " ")[:120],
        }
    )
    return found is not None, prompt_eval


def probe(
    variant: str, reps: int, timeout: float, ollama: str, health_url: str, log: Log
) -> int:
    note = _idle(health_url)
    if note.startswith("refuse:"):
        print(note, file=sys.stderr)
        return 2
    if note:
        log.write({"probe_health": note})
    (
        ollama_payload,
        inference_knobs,
        mode_table,
        sample_knobs,
        search_tool,
        tool_query,
        shape_messages,
    ) = _pair_imports()
    table = mode_table()
    models = [table["flash"], table["pro"]]
    failures: list[str] = []
    _preflight(failures, log, models, ollama)
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    tool = _tool_for(variant, search_tool)
    payload_fn = {
        "ollama": ollama_payload,
        "shape": shape_messages,
        "sample": sample_knobs,
        "knobs": inference_knobs(),
    }
    called = {model: {prompt: 0 for prompt in PROBE_SEARCH + FAST} for model in models}
    warm: dict[str, list[int]] = {model: [] for model in models}
    for model in models:
        for _rep in range(reps):
            for prompt in PROBE_SEARCH + FAST:
                try:
                    hit, prompt_eval = _probe_prompt(
                        log,
                        ollama,
                        payload_fn,
                        tool_query,
                        model,
                        prompt,
                        tool,
                        timeout,
                    )
                except Exception as error:
                    failures.append(
                        f"{model} {prompt}: {type(error).__name__}: {error}"
                    )
                    continue
                if hit:
                    called[model][prompt] += 1
                if prompt == "hi":
                    warm[model].append(prompt_eval)
    for model in models:
        for prompt in PROBE_SEARCH:
            if called[model][prompt] != reps:
                failures.append(
                    f"{model} {prompt!r} tool calls {called[model][prompt]}/{reps}"
                )
        for prompt in FAST:
            if called[model][prompt] != 0:
                failures.append(
                    f"{model} {prompt!r} unexpected tool calls {called[model][prompt]}"
                )
        samples = warm[model]
        last = samples[-1] if samples else 0
        if len(samples) < 2 or last <= 0 or last > 40:
            failures.append(f"{model} warm hi prompt_eval_count {last}")
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print("probe ok")
    return 0


def _host(base: str) -> str:
    stripped = base.split("://", 1)[-1]
    return stripped.split("/", 1)[0].split(":", 1)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--tunnel")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--probe-variant", choices=("a", "b"), default="a")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--health-url", default="http://127.0.0.1:18080/health")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--baseline-in")
    parser.add_argument("--baseline-out")
    parser.add_argument("--log-dir")
    args = parser.parse_args(argv)
    if args.reps < 1:
        print("reps must be at least 1", file=sys.stderr)
        return 2
    log = Log(args.log_dir)
    try:
        if args.probe:
            return probe(
                args.probe_variant,
                args.reps,
                args.timeout,
                args.ollama,
                args.health_url,
                log,
            )
        if not args.base or not args.tunnel:
            print("--base and --tunnel are required", file=sys.stderr)
            return 2
        failures: list[str] = []
        bounds = _load_bounds(args.baseline_in, failures)
        proof_failures, medians = run(
            args.base,
            args.tunnel,
            args.reps,
            args.timeout,
            bounds,
            log,
            _host(args.base),
        )
        failures.extend(proof_failures)
        if args.baseline_out:
            blob = json.dumps({"prompts": medians}, indent=2)
            Path(args.baseline_out).write_text(blob + "\n", encoding="utf-8")
            print(blob)
        if failures:
            for line in failures:
                print(f"FAIL {line}", file=sys.stderr)
            return 1
        print("ok")
        return 0
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
