#!/usr/bin/env python3
"""Time one /tools/embed call on this board. pi3 only. No chat path."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:18080/tools/embed"
TEXTS = ["hello there", "hi there"]


def main() -> int:
    payload = json.dumps({"texts": TEXTS}).encode()
    request = urllib.request.Request(
        URL, data=payload, headers={"content-type": "application/json"}
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=3.5) as response:
            body = json.loads(response.read().decode() or "{}")
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    elapsed = time.perf_counter() - started
    vectors = body.get("vectors") if isinstance(body, dict) else None
    width = 0
    if isinstance(vectors, list) and vectors and isinstance(vectors[0], list):
        width = len(vectors[0])
    report = {
        "ok": bool(isinstance(body, dict) and not body.get("skipped") and width),
        "skipped": "" if not isinstance(body, dict) else body.get("skipped") or "",
        "n": len(vectors) if isinstance(vectors, list) else 0,
        "width": width,
        "seconds": round(elapsed, 4),
    }
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
