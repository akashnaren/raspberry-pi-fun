#!/usr/bin/env python3
"""Time simultaneous chats and sample process RSS.

Stdlib only. Does not start or stop Ollama. On pi4, with the service already
up and qwen2.5:0.5b loaded:

  python3 scripts/bench_concurrent.py --url http://127.0.0.1:18080 --n 2 --rounds 5

Direct to the model server, same options the router sends:

  python3 scripts/bench_concurrent.py --ollama http://127.0.0.1:11434 --n 2 --rounds 5

RSS is the sum of VmRSS for the named process and its children, sampled while
the requests run. p50 and p95 are nearest-rank over the successful latencies.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def _family_rss_kib(comm: str) -> int:
    rows = []
    try:
        listing = os.listdir("/proc")
    except OSError:
        return 0
    for name in listing:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            command = open(f"/proc/{pid}/comm", encoding="utf-8").read().strip()
            status = open(f"/proc/{pid}/status", encoding="utf-8").read()
            stat = open(f"/proc/{pid}/stat", encoding="utf-8").read()
        except OSError:
            continue
        rss = 0
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1])
                break
        # stat: pid (comm) state ppid ...
        end = stat.rfind(")")
        ppid = int(stat[end + 2 :].split()[1]) if end != -1 else 0
        rows.append((pid, ppid, rss, command))
    roots = {
        pid
        for pid, _ppid, _rss, command in rows
        if command == comm or command.startswith(comm)
    }
    if not roots:
        return 0
    seen = set(roots)
    changed = True
    while changed:
        changed = False
        for pid, ppid, _rss, _command in rows:
            if ppid in seen and pid not in seen:
                seen.add(pid)
                changed = True
    return sum(rss for pid, _ppid, rss, _command in rows if pid in seen)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def _chat(url: str, payload: dict, timeout: float) -> tuple[int, float]:
    started = time.perf_counter()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "X-Pi-Target": "pi4",
            "X-Pi-Mesh": "off",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return response.status, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as error:
        error.read()
        return error.code, (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Concurrent chat latency and RSS")
    parser.add_argument(
        "--url", default="", help="Router origin, for example http://127.0.0.1:18080"
    )
    parser.add_argument(
        "--ollama", default="", help="Ollama origin, for example http://127.0.0.1:11434"
    )
    parser.add_argument(
        "--n", type=int, default=2, help="Simultaneous requests per round"
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--predict", type=int, default=32)
    parser.add_argument(
        "--rss-comm", default="ollama", help="Process name whose tree RSS is sampled"
    )
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    if bool(args.url) == bool(args.ollama):
        raise SystemExit("pass exactly one of --url or --ollama")
    if args.ollama:
        endpoint = args.ollama.rstrip("/") + "/api/chat"
        payload = {
            "model": "qwen2.5:0.5b",
            "messages": [
                {"role": "user", "content": "Reply with one short sentence about rain."}
            ],
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.7,
                "num_predict": args.predict,
                "num_ctx": 2048,
                "num_thread": 4,
                "num_batch": 128,
            },
        }
    else:
        endpoint = args.url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": "qwen2.5:0.5b",
            "messages": [
                {"role": "user", "content": "Reply with one short sentence about rain."}
            ],
            "stream": False,
            "temperature": 0.7,
            "max_tokens": args.predict,
        }
    latencies: list[float] = []
    statuses: list[int] = []
    peak = _family_rss_kib(args.rss_comm)
    samples = [peak]
    stop = False

    def sampler() -> None:
        nonlocal peak
        while not stop:
            rss = _family_rss_kib(args.rss_comm)
            samples.append(rss)
            if rss > peak:
                peak = rss
            time.sleep(0.15)

    import threading

    thread = threading.Thread(target=sampler, daemon=True)
    thread.start()
    try:
        for _round in range(args.rounds):
            with ThreadPoolExecutor(max_workers=args.n) as pool:
                rows = list(
                    pool.map(
                        lambda _i: _chat(endpoint, payload, args.timeout),
                        range(args.n),
                    )
                )
            for status, latency in rows:
                statuses.append(status)
                if status == 200:
                    latencies.append(latency)
    finally:
        stop = True
        thread.join(timeout=1)
    peak = max([peak, *samples])
    report = {
        "endpoint": endpoint,
        "simultaneous": args.n,
        "rounds": args.rounds,
        "ok": statuses.count(200),
        "statuses": statuses,
        "p50_ms": round(_percentile(latencies, 50), 1),
        "p95_ms": round(_percentile(latencies, 95), 1),
        "peak_rss_kib": peak,
        "peak_rss_mib": round(peak / 1024, 1),
        "rss_comm": args.rss_comm,
    }
    print(json.dumps(report))


if __name__ == "__main__":
    main()
