#!/usr/bin/env python3
"""Live proof for button search and the explicit-intent backup.

Stdlib only. Mesh on, Flash, except the cities prompt which uses auto.
Prints each turn. Exits non-zero when a check fails.

The Search control searches every turn. With it off, only an explicit ask
searches (search, look up, the web, tools, google, news, latest, current,
today). hi, 2+2, code, and a poem stay near the recorded baseline:
median total_ms at most max(baseline + 500, baseline * 1.2).

    python3 pi-pair/scripts/qa/search_decide_live.py \
        --base http://<pi4>:18080 --tunnel https://<brain-tunnel> [--reps 3]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

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
ORDINARY = (
    "how to follow people on X",
    "bath bombs recipe",
    "Stalker 1979 film",
    "track my package",
)
PRIMES = "List every prime number from 1 to 50."
CITIES = "What are the top 10 cities to visit in Europe?"
HORROR = "top 5 horror movies"
FLASH = "qwen3:0.6b"
_PRIME_VALUES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47)
_GARBLE = (
    "```",
    "<search>",
    "/no_think",
    "None of the provided notes",
    "<tool_call>",
)
_TESLA = re.compile(r"(?:former\s+)?(?:ceo|founder|chairman)\s+of\s+tesla", re.I)
_ITEM = re.compile(r"^(?:\d+[\.\)]|[-*•])\s+\S")
DEFAULT_TOTAL_MS = {
    "hi": 2132,
    "What is 2+2?": 711,
    "Write a Python function that reverses a string.": 3110,
    "Write a four-line poem about rain.": 7403,
}


def chat_body(prompt: str, web_search: bool | None = None, mode: str = "flash") -> dict:
    body = {
        "model": FLASH,
        "mode": mode,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
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
            parsed = {}
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


def garble(text: str) -> str:
    for token in _GARBLE:
        if token in (text or ""):
            return token
    return ""


def tesla_claim(reply: str, sources: list) -> str:
    """A Tesla title phrase that is not in the sources. Empty when clean."""
    blob = " ".join(
        " ".join(
            str(row.get(key) or "") for key in ("title", "snippet", "url", "context")
        )
        for row in sources
        if isinstance(row, dict)
    )
    for match in _TESLA.finditer(reply or ""):
        if re.search(re.escape(match.group(0)), blob, re.I) is None:
            return match.group(0)
    return ""


def list_items(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if _ITEM.match(line.strip()))


def _timing(body: dict, key: str) -> int:
    timing = body.get("pi_timing") if isinstance(body.get("pi_timing"), dict) else {}
    try:
        return int(timing.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _print_turn(prompt: str, body: dict, peer: str) -> None:
    sources = body.get("pi_sources") if isinstance(body.get("pi_sources"), list) else []
    text = reply_text(body).replace("\n", " ")
    print(
        json.dumps(
            {
                "prompt": prompt,
                "pi_stages": body.get("pi_stages"),
                "pi_decide": body.get("pi_decide"),
                "pi_decide_source": body.get("pi_decide_source"),
                "pi_search_query": body.get("pi_search_query"),
                "pi_search": body.get("pi_search"),
                "pi_sources": len(sources),
                "prefill_tokens": _timing(body, "prefill_tokens"),
                "total_ms": _timing(body, "total_ms"),
                "pi_peer": body.get("pi_peer"),
                "X-Pi-Peer": peer,
                "reply": text[:160],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def _median(values: list[int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _cool(failures: list[str], root: str, timeout: float) -> None:
    """Wait while the board is hotter than 80 C. Give up after three minutes."""
    deadline = time.monotonic() + 180
    while True:
        try:
            status, body, _peer = post_json(f"{root}/health", None, min(timeout, 10))
        except Exception as error:
            failures.append(f"health before turn: {type(error).__name__}: {error}")
            return
        if status != 200:
            return
        temp = body.get("temp_c")
        try:
            hot = temp is not None and float(temp) > 80
        except (TypeError, ValueError):
            return
        if not hot:
            return
        if time.monotonic() >= deadline:
            failures.append(f"temp_c stayed above 80: {temp}")
            return
        time.sleep(5)


def run(
    base: str, tunnel: str, reps: int, timeout: float, baselines: dict[str, float]
) -> tuple[list[str], dict]:
    failures: list[str] = []
    samples: dict[str, dict[str, list[int]]] = {
        prompt: {"total_ms": [], "prefill_tokens": []} for prompt in FAST
    }
    root = base.rstrip("/")
    for rep in range(reps):
        _cool(failures, root, timeout)
        if rep == 0:
            _check_stream(failures, root, timeout)
        _check_search(
            failures,
            root,
            NATAN,
            timeout,
            min_chars=40,
            claim=True,
            source="keyword",
        )
        for prompt in NEWS:
            _check_search(
                failures,
                root,
                prompt,
                timeout,
                min_chars=1,
                web_search=True,
                source="user",
            )
        for prompt in FAST:
            _check_fast(failures, root, prompt, timeout, samples)
        _check_toggle(failures, root, timeout)
    _check_regressions(failures, root, timeout)
    _check_yes(failures, root, timeout)
    _latency(failures, samples, baselines)
    _health(failures, root, tunnel, timeout)
    _models(root, timeout)
    medians = {
        prompt: {
            "total_ms": _median(rows["total_ms"]),
            "prefill_tokens": _median(rows["prefill_tokens"]),
        }
        for prompt, rows in samples.items()
    }
    return failures, medians


def _one(failures, root, prompt, timeout, web_search, mode="flash"):
    try:
        status, body, peer = post_json(
            f"{root}/v1/chat/completions",
            chat_body(prompt, web_search, mode),
            timeout,
        )
    except Exception as error:
        failures.append(f"{prompt}: {type(error).__name__}: {error}")
        return None, ""
    label = prompt if web_search is None else f"{prompt} web_search"
    _print_turn(label, body, peer)
    if status != 200:
        failures.append(f"{prompt} status {status}")
        return None, peer
    return body, peer


def _peer_ok(failures, body, peer, prompt) -> None:
    if body.get("pi_peer") != "pi4":
        failures.append(f"{prompt} pi_peer {body.get('pi_peer')!r}")
    if peer != "pi4":
        failures.append(f"{prompt} X-Pi-Peer {peer!r}")


def _clean(failures, prompt, text: str) -> None:
    token = garble(text)
    if token:
        failures.append(f"{prompt} reply contained {token!r}")


def _check_search(
    failures,
    root,
    prompt,
    timeout,
    min_chars,
    claim=False,
    web_search=None,
    source="keyword",
):
    body, peer = _one(failures, root, prompt, timeout, web_search)
    if body is None:
        return
    stages = body.get("pi_stages") or []
    if "searching" not in stages:
        failures.append(f"expected a search: {prompt}")
    if body.get("pi_decide") != "search":
        failures.append(f"{prompt} decision {body.get('pi_decide')!r}")
    if body.get("pi_decide_source") != source:
        failures.append(f"{prompt} source {body.get('pi_decide_source')!r}")
    if not str(body.get("pi_search_query") or "").strip():
        failures.append(f"{prompt} missing pi_search_query")
    text = reply_text(body)
    if len(text.strip()) < min_chars:
        failures.append(f"{prompt} reply length {len(text.strip())}")
    _clean(failures, prompt, text)
    _peer_ok(failures, body, peer, prompt)
    if not claim:
        return
    sources = body.get("pi_sources") if isinstance(body.get("pi_sources"), list) else []
    term = tesla_claim(text, sources)
    if term:
        failures.append(f"invented {term!r} for {prompt}")


def _check_fast(failures, root, prompt, timeout, samples) -> None:
    body, peer = _one(failures, root, prompt, timeout, None)
    if body is None:
        return
    stages = body.get("pi_stages") or []
    if "searching" in stages:
        failures.append(f"fast prompt searched: {prompt}")
    samples[prompt]["total_ms"].append(_timing(body, "total_ms"))
    samples[prompt]["prefill_tokens"].append(_timing(body, "prefill_tokens"))
    if body.get("pi_decide") or body.get("pi_decide_source"):
        failures.append(f"{prompt} stamped a decision {body.get('pi_decide_source')!r}")
    _clean(failures, prompt, reply_text(body))
    _peer_ok(failures, body, peer, prompt)


def _check_toggle(failures, root, timeout) -> None:
    forced, peer = _one(failures, root, "hi", timeout, True)
    if forced is None:
        return
    stages = forced.get("pi_stages") or []
    if "searching" not in stages:
        failures.append("toggle on did not search for hi")
    if forced.get("pi_decide_source") != "user":
        failures.append(f"toggle source was {forced.get('pi_decide_source')!r}")
    if forced.get("pi_decide") != "search":
        failures.append(f"toggle decision was {forced.get('pi_decide')!r}")
    text = reply_text(forced)
    if not text.strip():
        failures.append("toggle reply was empty")
    _clean(failures, "hi web_search", text)
    _peer_ok(failures, forced, peer, "hi web_search")


def _check_regressions(failures, root, timeout) -> None:
    _cool(failures, root, timeout)
    body, peer = _one(failures, root, PRIMES, timeout, None)
    if body is not None:
        _skip_ok(failures, PRIMES, body, peer)
        text = reply_text(body)
        for number in _PRIME_VALUES:
            if re.search(rf"(?<!\d){number}(?!\d)", text) is None:
                failures.append(f"primes reply missing {number}")
                break
    body, peer = _one(failures, root, CITIES, timeout, None, mode="auto")
    if body is not None:
        _skip_ok(failures, CITIES, body, peer)
        count = list_items(reply_text(body))
        if count < 10:
            failures.append(f"cities list items {count} < 10")
    for prompt in ORDINARY:
        body, peer = _one(failures, root, prompt, timeout, None)
        if body is None:
            continue
        _skip_ok(failures, prompt, body, peer)
    body, peer = _one(failures, root, HORROR, timeout, None)
    if body is not None:
        _skip_ok(failures, HORROR, body, peer)
        count = list_items(reply_text(body))
        if count < 5:
            failures.append(f"horror list items {count} < 5")


def _skip_ok(failures, prompt, body, peer) -> None:
    stages = body.get("pi_stages") or []
    if "searching" in stages:
        failures.append(f"regression searched: {prompt}")
    text = reply_text(body)
    if not text.strip():
        failures.append(f"{prompt} reply was empty")
    _clean(failures, prompt, text)
    _peer_ok(failures, body, peer, prompt)


def _check_yes(failures, root, timeout) -> None:
    payload = chat_body("hi")
    payload["web_search"] = "yes"
    try:
        status, body, _peer = post_json(
            f"{root}/v1/chat/completions",
            payload,
            timeout,
        )
    except Exception as error:
        failures.append(f"web_search yes: {type(error).__name__}: {error}")
        return
    print(json.dumps({"web_search_yes": status, "error": body.get("error")}))
    if status != 400:
        failures.append(f"web_search yes status {status}")
    if body.get("error") != "web_search must be true or false.":
        failures.append(f"web_search yes error {body.get('error')!r}")


def _check_stream(failures, root, timeout) -> None:
    payload = chat_body(NATAN)
    payload["stream"] = True
    try:
        request = urllib.request.Request(
            f"{root}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            peer = response.headers.get("X-Pi-Peer") or ""
            raw = response.read().decode()
    except Exception as error:
        failures.append(f"stream {NATAN}: {type(error).__name__}: {error}")
        return
    events = []
    parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            item = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        events.append(item)
        choices = item.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            continue
        delta = choices[0].get("delta") or {}
        if isinstance(delta, dict) and delta.get("content"):
            parts.append(str(delta["content"]))
    text = "".join(parts)
    statuses = [item.get("pi_status") for item in events]
    finals = [item for item in events if item.get("pi_decide")]
    print(
        json.dumps(
            {
                "stream": NATAN,
                "status": status,
                "pi_status": statuses,
                "pi_decide": finals[-1].get("pi_decide") if finals else None,
                "reply": text[:160],
            },
            ensure_ascii=False,
        )
    )
    if status != 200:
        failures.append(f"stream status {status}")
    if "searching" not in statuses:
        failures.append("stream did not search")
    if not finals or finals[-1].get("pi_decide") != "search":
        failures.append("stream decision was not search")
    if not finals or finals[-1].get("pi_decide_source") != "keyword":
        failures.append("stream source was not keyword")
    if not finals or not str(finals[-1].get("pi_search_query") or "").strip():
        failures.append("stream missing pi_search_query")
    if len(text.strip()) < 40:
        failures.append(f"stream reply length {len(text.strip())}")
    _clean(failures, "stream " + NATAN, text)
    sources = []
    if finals and isinstance(finals[-1].get("pi_sources"), list):
        sources = finals[-1]["pi_sources"]
    term = tesla_claim(text, sources)
    if term:
        failures.append(f"stream invented {term!r}")
    if peer != "pi4":
        failures.append(f"stream X-Pi-Peer {peer!r}")
    if "HTTP/1." in raw:
        failures.append("stream wrote a second HTTP status")


def _latency(failures, samples, baselines) -> None:
    hi_prefills = samples["hi"]["prefill_tokens"]
    warm = hi_prefills[1:] if len(hi_prefills) > 1 else hi_prefills
    for value in warm:
        if value > 60:
            failures.append(f"warm hi prefill_tokens {value} > 60")
    for prompt, rows in samples.items():
        if not rows["total_ms"]:
            continue
        base = baselines.get(prompt)
        if base is None:
            failures.append(f"no baseline for {prompt}")
            continue
        limit = max(float(base) + 500.0, float(base) * 1.2)
        total = _median(rows["total_ms"])
        if total > limit:
            failures.append(
                f"{prompt} median total_ms {total} > {limit} (baseline {base})"
            )


def _health(failures, root, tunnel, timeout) -> None:
    try:
        status, body, _peer = post_json(f"{root}/health", None, timeout)
    except Exception as error:
        failures.append(f"health: {type(error).__name__}: {error}")
        return
    print(json.dumps({"health_status": status, "peers_up": body.get("peers_up")}))
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
    print(json.dumps({"tunnel_search": code}))
    if code != 403:
        failures.append(f"tunnel /v1/search returned {code}")


def _models(root: str, timeout: float) -> None:
    """Ollama's /api/ps is not on the router. A 404 is expected and not a failure."""
    try:
        code, _body, _peer = post_json(f"{root}/api/ps", None, min(timeout, 10))
    except Exception as error:
        print(json.dumps({"api_ps": type(error).__name__}))
        return
    print(json.dumps({"api_ps": code}))


def _load_baselines(path: str, failures: list[str]) -> dict[str, float]:
    baselines = dict(DEFAULT_TOTAL_MS)
    try:
        with open(path, encoding="utf-8") as handle:
            saved = json.loads(handle.read())
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"baseline: {error}")
        return baselines
    saved_rows = saved.get("prompts") if isinstance(saved, dict) else None
    if not isinstance(saved_rows, dict):
        failures.append("baseline file has no prompts")
        return baselines
    for prompt in FAST:
        previous = saved_rows.get(prompt) or {}
        try:
            baselines[prompt] = float(previous.get("total_ms"))
        except (TypeError, ValueError):
            failures.append(f"baseline missing total_ms for {prompt}")
    return baselines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--tunnel", required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--baseline-in")
    parser.add_argument("--baseline-out")
    args = parser.parse_args(argv)
    if args.reps < 1:
        print("reps must be at least 1", file=sys.stderr)
        return 2
    early: list[str] = []
    baselines = (
        _load_baselines(args.baseline_in, early)
        if args.baseline_in
        else dict(DEFAULT_TOTAL_MS)
    )
    failures, medians = run(args.base, args.tunnel, args.reps, args.timeout, baselines)
    failures = early + failures
    if args.baseline_out:
        blob = json.dumps({"prompts": medians}, indent=2)
        with open(args.baseline_out, "w", encoding="utf-8") as handle:
            handle.write(blob + "\n")
        print(blob)
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
