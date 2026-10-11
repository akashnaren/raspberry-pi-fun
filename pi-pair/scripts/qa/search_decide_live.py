#!/usr/bin/env python3
"""Live proof for the model search check and the composer force flag.

Stdlib only. Non-streaming LAN posts, mesh on, Flash. Prints the stage list,
the decision, the source count, and the first 160 characters of each reply.
Exits non-zero when a check fails.

    python3 pi-pair/scripts/qa/search_decide_live.py \
        --base http://<pi4>:18080 --tunnel https://<brain-tunnel> [--reps 3]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.error
import urllib.request

NATAN = "Who is Natan Weitzman from Tesla - use your tools web search"
NEWS = (
    "What is the latest news?",
    "What is the latest news about NASA today?",
)
FAST = (
    "hi",
    "What is 2+2?",
    "Write a Python function that reverses a string.",
    "Write a four-line poem about rain.",
)
_CLAIM = re.compile(r"\b(CEO|chief executive|founder|co-?founder)\b", re.I)
FLASH = "qwen3:0.6b"


def chat_body(prompt: str, web_search: bool | None = None) -> dict:
    body = {
        "model": FLASH,
        "mode": "flash",
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


def invented_claim(reply: str, sources: list) -> str:
    """A title word that is not in a source title. Empty when the reply is clean."""
    titles = " ".join(
        str(row.get("title") or "") for row in sources if isinstance(row, dict)
    )
    for match in _CLAIM.finditer(reply or ""):
        term = match.group(1)
        if re.search(re.escape(term), titles, re.I) is None:
            return term
    return ""


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
                "pi_search": body.get("pi_search"),
                "pi_sources": len(sources),
                "decide_ms": _timing(body, "decide_ms"),
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


def run(base: str, tunnel: str, reps: int, timeout: float) -> tuple[list[str], dict]:
    failures: list[str] = []
    samples: dict[str, dict[str, list[int]]] = {
        prompt: {"decide_ms": [], "total_ms": []} for prompt in FAST
    }
    root = base.rstrip("/")
    for _rep in range(reps):
        _check_search(
            failures,
            root,
            NATAN,
            timeout,
            want_search=True,
            source="model",
            claim=True,
        )
        for prompt in NEWS:
            _check_search(
                failures, root, prompt, timeout, want_search=True, source="model"
            )
        for prompt in FAST:
            body, peer = _one(failures, root, prompt, timeout, None)
            if body is None:
                continue
            stages = body.get("pi_stages") or []
            if "searching" in stages:
                failures.append(f"fast prompt searched: {prompt}")
            decide_ms = _timing(body, "decide_ms")
            total_ms = _timing(body, "total_ms")
            samples[prompt]["decide_ms"].append(decide_ms)
            samples[prompt]["total_ms"].append(total_ms)
            if prompt == "What is 2+2?" and decide_ms != 0:
                failures.append(f"2+2 decide_ms was {decide_ms}")
            _peer_ok(failures, body, peer, prompt)
        forced, forced_peer = _one(failures, root, "hi", timeout, True)
        if forced is not None:
            stages = forced.get("pi_stages") or []
            if "searching" not in stages:
                failures.append("toggle on did not search for hi")
            if _timing(forced, "decide_ms") != 0:
                failures.append("toggle on spent decide_ms on hi")
            if forced.get("pi_decide_source") != "user":
                failures.append(f"toggle source was {forced.get('pi_decide_source')!r}")
            if forced.get("pi_decide") != "search":
                failures.append(f"toggle decision was {forced.get('pi_decide')!r}")
            _peer_ok(failures, forced, forced_peer, "hi web_search")
    for prompt, rows in samples.items():
        if prompt == "What is 2+2?":
            continue
        if not rows["decide_ms"]:
            continue
        decide = _median(rows["decide_ms"])
        if decide > 1200:
            failures.append(f"{prompt} median decide_ms {decide} > 1200")
    _health(failures, root, tunnel, timeout)
    medians = {
        prompt: {
            "decide_ms": _median(rows["decide_ms"]),
            "total_ms": _median(rows["total_ms"]),
        }
        for prompt, rows in samples.items()
    }
    return failures, medians


def _one(failures, root, prompt, timeout, web_search):
    try:
        status, body, peer = post_json(
            f"{root}/v1/chat/completions",
            chat_body(prompt, web_search),
            timeout,
        )
    except Exception as error:
        failures.append(f"{prompt}: {type(error).__name__}: {error}")
        return None, ""
    _print_turn(prompt if web_search is None else f"{prompt} web_search", body, peer)
    if status != 200:
        failures.append(f"{prompt} status {status}")
        return None, peer
    return body, peer


def _peer_ok(failures, body, peer, prompt) -> None:
    if body.get("pi_peer") != "pi4":
        failures.append(f"{prompt} pi_peer {body.get('pi_peer')!r}")
    if peer != "pi4":
        failures.append(f"{prompt} X-Pi-Peer {peer!r}")


def _check_search(failures, root, prompt, timeout, want_search, source, claim=False):
    body, peer = _one(failures, root, prompt, timeout, None)
    if body is None:
        return
    stages = body.get("pi_stages") or []
    if want_search and "searching" not in stages:
        failures.append(f"expected a search: {prompt}")
    if body.get("pi_decide_source") != source:
        failures.append(f"{prompt} source {body.get('pi_decide_source')!r}")
    if want_search and body.get("pi_decide") != "search":
        failures.append(f"{prompt} decision {body.get('pi_decide')!r}")
    _peer_ok(failures, body, peer, prompt)
    if not claim:
        return
    sources = body.get("pi_sources") if isinstance(body.get("pi_sources"), list) else []
    term = invented_claim(reply_text(body), sources)
    if term:
        failures.append(f"invented {term!r} for {prompt}")


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


def _compare_baseline(failures, medians, path: str) -> None:
    try:
        with open(path, encoding="utf-8") as handle:
            saved = json.loads(handle.read())
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"baseline: {error}")
        return
    saved_rows = saved.get("prompts") if isinstance(saved, dict) else None
    if not isinstance(saved_rows, dict):
        failures.append("baseline file has no prompts")
        return
    for prompt, rows in medians.items():
        if prompt == "What is 2+2?":
            continue
        previous = saved_rows.get(prompt) or {}
        try:
            base = float(previous.get("total_ms"))
        except (TypeError, ValueError):
            failures.append(f"baseline missing total_ms for {prompt}")
            continue
        if abs(rows["total_ms"] - base) > 1500:
            failures.append(
                f"{prompt} median total_ms {rows['total_ms']} vs baseline {base}"
            )


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
    failures, medians = run(args.base, args.tunnel, args.reps, args.timeout)
    if args.baseline_out:
        blob = json.dumps({"prompts": medians}, indent=2)
        with open(args.baseline_out, "w", encoding="utf-8") as handle:
            handle.write(blob + "\n")
        print(blob)
    if args.baseline_in:
        _compare_baseline(failures, medians, args.baseline_in)
    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
