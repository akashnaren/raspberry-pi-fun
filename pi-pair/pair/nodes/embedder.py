"""On-demand Arctic :xs for the pi3 train fold.

The tag loads only when embed() runs, on loopback, and OCR unloads it first.
This module does not import chat, the gate, the scheduler, the queue, images,
or memory. Callers never see an exception from embed().
"""

from __future__ import annotations

import json
import math
import os
import platform
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

EMBED_MODEL = "snowflake-arctic-embed:xs"
OLLAMA = "http://127.0.0.1:11434"
EMBED_TIMEOUT_S = 2.5
MAX_TEXTS = 16
MAX_CHARS = 512
KEEP_ALIVE = "30s"
DRAIN_S = 1.5
UNLOAD_S = 2.0
MIN_AVAILABLE_MB = 250
_PS_TIMEOUT_S = 0.5
_PS_CACHE_S = 5.0

_COND = threading.Condition()
_ocr_active = 0
_embed_active = 0
_ps_cache: tuple[float, str] | None = None
_PS_LOCK = threading.Lock()


def enabled() -> bool:
    """True only on the dataset role, on arm64, unless PI_PAIR_EMBED=0."""
    if os.environ.get("PI_PAIR_EMBED", "").strip() == "0":
        return False
    if os.environ.get("PI_PAIR_ROLE", "").strip().lower() != "dataset":
        return False
    return platform.machine().lower() in {"aarch64", "arm64"}


def _result(vectors: list, skipped: str) -> dict:
    return {
        "ok": True,
        "vectors": vectors,
        "model": EMBED_MODEL,
        "skipped": skipped,
    }


def mem_available_mb() -> float:
    """MemAvailable from /proc/meminfo, in megabytes. 0 when it cannot be read."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return 0.0
    for line in text.splitlines():
        if not line.startswith("MemAvailable:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return 0.0
        try:
            return float(parts[1]) / 1024.0
        except ValueError:
            return 0.0
    return 0.0


def _vectors_ok(rows: object, count: int) -> list[list[float]] | None:
    if not isinstance(rows, list) or len(rows) != count:
        return None
    width: int | None = None
    parsed: list[list[float]] = []
    for row in rows:
        if not isinstance(row, list) or not row:
            return None
        numbers: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            number = float(value)
            if not math.isfinite(number):
                return None
            numbers.append(number)
        if width is None:
            width = len(numbers)
        elif len(numbers) != width:
            return None
        parsed.append(numbers)
    return parsed


def _post(path: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        OLLAMA + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode() or "{}")
    if not isinstance(body, dict):
        raise ValueError("embed body")
    return body


def embed(texts: object) -> dict:
    """Embed texts. A skip is a normal result. This never raises."""
    global _embed_active
    try:
        if not enabled():
            return _result([], "off")
        if not isinstance(texts, list):
            return _result([], "error")
        if len(texts) > MAX_TEXTS:
            return _result([], "too_many")
        cleaned: list[str] = []
        for item in texts:
            if not isinstance(item, str):
                return _result([], "error")
            cleaned.append(item[:MAX_CHARS])
        if not cleaned:
            return _result([], "")
        with _COND:
            if _ocr_active > 0:
                return _result([], "ocr_busy")
            if mem_available_mb() < MIN_AVAILABLE_MB:
                return _result([], "low_memory")
            _embed_active += 1
        try:
            body = _post(
                "/api/embed",
                {
                    "model": EMBED_MODEL,
                    "input": cleaned,
                    "keep_alive": KEEP_ALIVE,
                    "truncate": True,
                },
                EMBED_TIMEOUT_S,
            )
        except TimeoutError:
            return _result([], "timeout")
        except urllib.error.HTTPError as exc:
            reason = "missing" if exc.code == 404 else "error"
            return _result([], reason)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return _result([], "missing")
        finally:
            with _COND:
                _embed_active -= 1
                _COND.notify_all()
        parsed = _vectors_ok(body.get("embeddings"), len(cleaned))
        if parsed is None:
            return _result([], "error")
        _clear_ps_cache()
        return _result(parsed, "")
    except Exception:
        return _result([], "error")


def ocr_enter() -> None:
    """Block new embeds and unload the tag before the first OCR binary runs."""
    global _ocr_active
    if not enabled():
        return
    first = False
    with _COND:
        _ocr_active += 1
        first = _ocr_active == 1
        if first:
            deadline = time.monotonic() + DRAIN_S
            while _embed_active > 0 and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                _COND.wait(remaining)
    if first:
        unload()


def ocr_exit() -> None:
    """Release one OCR admission. This does not load the tag."""
    global _ocr_active
    if not enabled():
        return
    with _COND:
        if _ocr_active > 0:
            _ocr_active -= 1
            _COND.notify_all()


def unload() -> None:
    """Best-effort drop of the tag. Errors are ignored."""
    if not enabled():
        return
    try:
        _post(
            "/api/embed",
            {"model": EMBED_MODEL, "input": [], "keep_alive": 0},
            UNLOAD_S,
        )
    except Exception:
        try:
            subprocess.run(
                ["ollama", "stop", EMBED_MODEL],
                timeout=UNLOAD_S,
                check=False,
                capture_output=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    _clear_ps_cache()


def _clear_ps_cache() -> None:
    global _ps_cache
    with _PS_LOCK:
        _ps_cache = None


def _store_ps_cache(value: str) -> None:
    global _ps_cache
    with _PS_LOCK:
        _ps_cache = (time.monotonic(), value)


def _get(path: str, timeout: float) -> dict:
    request = urllib.request.Request(OLLAMA + path)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode() or "{}")
    if not isinstance(body, dict):
        raise ValueError("embed body")
    return body


def _read_ps() -> str:
    try:
        body = _get("/api/ps", _PS_TIMEOUT_S)
    except TimeoutError:
        return "missing"
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return "missing"
    models = body.get("models")
    if not isinstance(models, list):
        return "missing"
    for row in models:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("model") or "")
        if name == EMBED_MODEL or name.startswith(EMBED_MODEL + ":"):
            return "loaded"
    return "unloaded"


def state() -> str:
    """off, missing, unloaded, loaded, or ocr_busy. /api/ps is capped at 0.5s."""
    if not enabled():
        return "off"
    with _COND:
        if _ocr_active > 0:
            return "ocr_busy"
    now = time.monotonic()
    with _PS_LOCK:
        cached = _ps_cache
        if cached is not None and now - cached[0] < _PS_CACHE_S:
            return cached[1]
    try:
        value = _read_ps()
    except Exception:
        value = "missing"
    _store_ps_cache(value)
    return value


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity. A length mismatch or a zero vector is 0."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for one, two in zip(left, right):
        dot += one * two
        left_norm += one * one
        right_norm += two * two
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)
