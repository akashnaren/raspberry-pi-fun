#!/usr/bin/env python3
"""Time to first token and total time for one streamed chat.

Stdlib only. Two modes:

  python3 scripts/bench/ttft.py
      Local mock Ollama (token drip) and the pair router. Search is a short
      stub so the number includes that hop without DuckDuckGo.

  python3 scripts/bench/ttft.py --url http://127.0.0.1:18080
      The same prompt against a live router. No mock is started.

Prints nearest-rank p50 and p95 for TTFT and total time, in milliseconds.
A warmup request is not included. The generation cap is left at 2.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROMPT = "Where is the east window kettle kept?"
REPEAT = "Where is the east window kettle kept?"
DRIP = (
    "The ",
    "kettle ",
    "is ",
    "in ",
    "the ",
    "hall ",
    "beside ",
    "the ",
    "east ",
    "window.",
)
TOKEN_PAUSE_S = 0.02
SEARCH_PAUSE_S = 0.03


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _summary(label: str, rows: list[tuple[float, float]]) -> dict:
    ttft = [item[0] for item in rows if item[0] is not None]
    total = [item[1] for item in rows]
    return {
        "label": label,
        "n": len(rows),
        "ttft_p50_ms": round(_percentile(ttft, 50) * 1000, 1) if ttft else None,
        "ttft_p95_ms": round(_percentile(ttft, 95) * 1000, 1) if ttft else None,
        "total_p50_ms": round(_percentile(total, 50) * 1000, 1) if total else None,
        "total_p95_ms": round(_percentile(total, 95) * 1000, 1) if total else None,
    }


def _print_summary(row: dict) -> None:
    print(
        f"{row['label']}: n={row['n']} "
        f"ttft p50={row['ttft_p50_ms']}ms p95={row['ttft_p95_ms']}ms "
        f"total p50={row['total_p50_ms']}ms p95={row['total_p95_ms']}ms",
        flush=True,
    )


def measure_stream(url: str, prompt: str, timeout: float) -> tuple[float | None, float]:
    """TTFT is the first SSE delta that carries assistant content."""
    payload = {
        "model": "qwen3:0.6b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "pi_mode": "flash",
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "X-Pi-Target": "pi4",
            "X-Pi-Mesh": "on",
        },
    )
    started = time.perf_counter()
    ttft = None
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while True:
            line = response.readline()
            if not line:
                break
            text = line.strip()
            if text == b"data: [DONE]":
                break
            if not text.startswith(b"data:"):
                continue
            try:
                event = json.loads(text[5:].decode())
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta") or {}
            content = delta.get("content")
            if content and ttft is None:
                ttft = time.perf_counter() - started
    return ttft, time.perf_counter() - started


def _run_series(
    url: str, prompt: str, runs: int, timeout: float
) -> list[tuple[float, float]]:
    rows = []
    for _ in range(runs):
        rows.append(measure_stream(url, prompt, timeout))
    return rows


class _Drip(BaseHTTPRequestHandler):
    """Tiny Ollama. Chat tokens pause between NDJSON lines."""

    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:
        if self.path.split("?")[0] != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {"models": [{"name": "qwen3:0.6b"}, {"name": "qwen3:1.7b"}]}
        ).encode()
        self._json(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            last = len(DRIP) - 1
            for index, token in enumerate(DRIP):
                time.sleep(TOKEN_PAUSE_S)
                line = {
                    "message": {"content": token},
                    "done": index == last,
                    "done_reason": "stop" if index == last else "",
                }
                self.wfile.write(json.dumps(line).encode() + b"\n")
                self.wfile.flush()
            return
        self._json(
            json.dumps(
                {"message": {"content": "".join(DRIP)}, "done_reason": "stop"}
            ).encode()
        )

    def _json(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(httpd: ThreadingHTTPServer) -> None:
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def bench_mock(runs: int) -> list[dict]:
    os.environ["PI_PAIR_ROLE"] = "brain"
    os.environ["PI_PAIR_NAME"] = "pi4"
    os.environ["PI_PAIR_REMOTE_SEARCH"] = "0"
    os.environ["PI_PAIR_HOST"] = "127.0.0.1"
    os.environ["PI_PAIR_PORT"] = "0"
    os.environ.setdefault("PI_PAIR_SLOTS", "2")
    tmp = tempfile.TemporaryDirectory()
    os.environ["PI_PAIR_DATA"] = tmp.name
    canned = Path(tmp.name) / "canned_map.json"
    canned.write_text('{"unrelated bench key": "not this answer"}\n', encoding="utf-8")
    os.environ["PI_PAIR_CANNED"] = str(canned)

    import pair.core.runtime as runtime
    from pair.routes import search as search_routes
    from pair.server import make_server

    ollama = ThreadingHTTPServer(("127.0.0.1", 0), _Drip)
    _start(ollama)
    runtime.configure()
    runtime.set_peers(
        [
            {
                "name": "pi4",
                "host": "127.0.0.1",
                "port": ollama.server_address[1],
                "kind": "ollama",
                "generative": True,
                "role": "brain",
                "note": "",
            }
        ]
    )
    runtime.set_infer_slots(2)

    def slow_search(query, opener=None):
        del query, opener
        time.sleep(SEARCH_PAUSE_S)
        return {"status": "failed", "sources": [], "context": ""}

    remembered: dict[str, dict] = {}

    def cached_search(query, opener=None):
        text = " ".join((query or "").split())
        hit = remembered.get(text)
        if hit is not None:
            return hit
        found = slow_search(query, opener)
        remembered[text] = found
        return found

    original_lookup = search_routes.lookup_web
    search_routes.lookup_web = slow_search
    router = make_server("127.0.0.1", 0)
    _start(router)
    url = f"http://127.0.0.1:{router.server_address[1]}/v1/chat/completions"
    try:
        measure_stream(url, PROMPT, timeout=30)
        cold = _summary("mock-cold", _run_series(url, PROMPT, runs, 30))
        # The real lookup caches a query. The repeat series uses that path.
        search_routes.lookup_web = cached_search
        measure_stream(url, REPEAT, timeout=30)
        repeat = _summary("mock-repeat", _run_series(url, REPEAT, runs, 30))
        return [cold, repeat]
    finally:
        router.shutdown()
        ollama.shutdown()
        search_routes.lookup_web = original_lookup
        tmp.cleanup()


def bench_live(url: str, runs: int, prompt: str) -> list[dict]:
    target = url.rstrip("/")
    if not target.endswith("/v1/chat/completions"):
        target += "/v1/chat/completions"
    measure_stream(target, prompt, timeout=180)
    return [_summary("live", _run_series(target, prompt, runs, 180))]


def main() -> None:
    parser = argparse.ArgumentParser(description="p50/p95 TTFT and total chat time")
    parser.add_argument(
        "--url", default="", help="Live router origin or /v1/chat/completions URL"
    )
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument("--prompt", default=PROMPT)
    args = parser.parse_args()
    runs = max(5, int(args.runs))
    if args.url:
        rows = bench_live(args.url, runs, args.prompt)
    else:
        rows = bench_mock(runs)
    for row in rows:
        _print_summary(row)
    print(json.dumps(rows), flush=True)


if __name__ == "__main__":
    main()
