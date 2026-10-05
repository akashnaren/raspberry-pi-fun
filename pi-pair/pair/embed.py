"""Arctic embed near-match for the canned map.

Exact keys stay on normalize_key. This module runs only after that miss.
It returns a stored answer when the best cosine is at least 0.85. It does
not generate. Stock Qwen (qwen2.5:0.5b) still answers when the score is
under that bar or Ollama cannot embed.

Pull the embed model once on pi4:

    ollama pull snowflake-arctic-embed:m

Arctic M is about 219MB there and about 0.1s per query once it is loaded.
Key vectors are cached beside the map as ``<stem>.vectors.json``.
"""
from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
import urllib.parse
from http import client as http_client
from pathlib import Path

EMBED_MODEL = "snowflake-arctic-embed:m"
PULL_COMMAND = "ollama pull snowflake-arctic-embed:m"
COSINE_THRESHOLD = 0.85

# A cold load of the 219MB model can take a few seconds. Connect stays
# short so an unreachable pi4 does not hold the miss that still goes to Qwen.
_CONNECT_TIMEOUT = 1.0
_READ_TIMEOUT = 20.0
_DOWN_TTL = 60.0

_LOCK = threading.Lock()
_MEMORY: dict[str, dict[str, list[float]]] = {}
_down_until = 0.0


def cosine(left: list[float], right: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Mismatched or empty vectors score 0."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for x_value, y_value in zip(left, right, strict=True):
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            return 0.0
        dot += x_value * y_value
        left_norm += x_value * x_value
        right_norm += y_value * y_value
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    score = dot / math.sqrt(left_norm * right_norm)
    if not math.isfinite(score):
        return 0.0
    return score


def vectors_path(map_file: Path) -> Path:
    path = Path(map_file)
    return path.with_name(path.stem + ".vectors.json")


def embed_texts(
    texts: list[str],
    *,
    url: str,
    model: str = EMBED_MODEL,
    timeout: float | None = None,
) -> list[list[float]]:
    """POST Ollama ``/api/embed`` and return one vector per input string."""
    rows = [str(text) for text in texts]
    if not rows:
        return []
    payload = {"model": model, "input": rows}
    body = _post_json(_endpoint(url), payload, _read_timeout(timeout))
    return _vectors(body, len(rows))


def ollama_embed_url() -> str | None:
    """Base URL of the generative Ollama peer, or ``PI_PAIR_EMBED_URL``."""
    if _disabled():
        return None
    override = os.environ.get("PI_PAIR_EMBED_URL", "").strip()
    if override:
        return override.rstrip("/")
    try:
        from pair.config import load_peers

        peers = load_peers()
    except Exception:
        return None
    for peer in peers:
        if peer.get("kind") == "ollama" and peer.get("generative"):
            return f"http://{peer['host']}:{peer['port']}"
    return None


def semantic_lookup(
    text: str,
    table: dict[str, str],
    *,
    map_file: Path | None = None,
    embedder=None,
    threshold: float | None = None,
) -> str | None:
    """Stored answer with the highest cosine at or above the bar.

    ``embedder`` replaces the live ``/api/embed`` client. Callers pass the
    normalized key and the folded map. Embed failures are misses.
    """
    query = text or ""
    if not query or not table:
        return None
    bar = COSINE_THRESHOLD if threshold is None else float(threshold)
    if not math.isfinite(bar):
        bar = COSINE_THRESHOLD
    live = embedder is None
    if live:
        if _disabled() or _is_down():
            return None
        url = ollama_embed_url()
        if not url:
            return None
        client = _live_client(url)
    else:
        client = embedder
    try:
        vectors = _ensure_vectors(table, map_file, client)
        found = client([query])
        if not isinstance(found, list) or len(found) != 1:
            raise ValueError("embed count")
        query_vec = found[0]
    except Exception:
        if live:
            _mark_down()
        return None
    return _best(table, vectors, query_vec, bar)


def forget_vectors(map_file: Path) -> None:
    """Drop the sidecar and the in-memory vectors for one map file."""
    path = Path(map_file)
    with _LOCK:
        _MEMORY.pop(_canonical(path), None)
    try:
        vectors_path(path).unlink()
    except OSError:
        pass


def reset_embed_state() -> None:
    """Clear the in-memory cache and the down timer. Sidecar files stay."""
    global _down_until
    with _LOCK:
        _MEMORY.clear()
        _down_until = 0.0


def _live_client(url: str):
    def embedder(texts: list[str]) -> list[list[float]]:
        return embed_texts(texts, url=url)

    return embedder


def _disabled() -> bool:
    flag = os.environ.get("PI_PAIR_EMBED", "").strip().lower()
    return flag in {"0", "off", "false", "no"}


def _is_down() -> bool:
    with _LOCK:
        return time.monotonic() < _down_until


def _mark_down() -> None:
    global _down_until
    with _LOCK:
        _down_until = time.monotonic() + _DOWN_TTL


def _canonical(map_file: Path) -> str:
    try:
        return str(Path(map_file).resolve())
    except OSError:
        return str(map_file)


def _read_timeout(override: float | None) -> float:
    if override is not None:
        return float(override) if override > 0 else _READ_TIMEOUT
    raw = os.environ.get("PI_PAIR_EMBED_TIMEOUT", "").strip()
    if not raw:
        return _READ_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return _READ_TIMEOUT
    if value <= 0 or not math.isfinite(value):
        return _READ_TIMEOUT
    return value


def _endpoint(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if not raw:
        raise ValueError("embed url is empty")
    if raw.endswith("/api/embed"):
        return raw
    return raw + "/api/embed"


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("embed url must be http")
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    body = json.dumps(payload).encode()
    sock = socket.create_connection((parsed.hostname, port), timeout=min(_CONNECT_TIMEOUT, timeout))
    connection = http_client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    connection.sock = sock
    try:
        sock.settimeout(timeout)
        connection.request(
            "POST",
            path,
            body=body,
            headers={"content-type": "application/json"},
        )
        response = connection.getresponse()
        raw = response.read()
        status = response.status
    finally:
        connection.close()
    if status != 200:
        raise OSError(f"embed status {status}")
    data = json.loads(raw.decode())
    if not isinstance(data, dict):
        raise ValueError("embed response must be an object")
    return data


def _vectors(body: dict, count: int) -> list[list[float]]:
    raw = body.get("embeddings")
    if raw is None and count == 1 and isinstance(body.get("embedding"), list):
        raw = [body["embedding"]]
    if not isinstance(raw, list) or len(raw) != count:
        raise ValueError("embed response missing embeddings")
    vectors: list[list[float]] = []
    for item in raw:
        if not _valid(item):
            raise ValueError("embed response missing embeddings")
        vectors.append([float(number) for number in item])
    return vectors


def _valid(vec: object) -> bool:
    if not isinstance(vec, list) or not vec:
        return False
    for number in vec:
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return False
        if not math.isfinite(float(number)):
            return False
    return True


def _ensure_vectors(table: dict[str, str], map_file: Path | None, embedder) -> dict[str, list[float]]:
    keys = [key for key in table if isinstance(key, str) and key]
    stored: dict[str, list[float]] = {}
    canonical = ""
    if map_file is not None:
        path = Path(map_file)
        canonical = _canonical(path)
        with _LOCK:
            cached = _MEMORY.get(canonical)
        if cached is not None and set(cached) == set(keys):
            return {key: cached[key] for key in keys}
        stored = {key: vec for key, vec in _read_store(path).items() if key in table}
    missing = [key for key in sorted(keys) if key not in stored]
    if missing:
        fresh = embedder(missing)
        if not isinstance(fresh, list) or len(fresh) != len(missing):
            raise ValueError("embed count")
        for key, vec in zip(missing, fresh, strict=True):
            if not _valid(vec):
                raise ValueError("embed vector")
            stored[key] = [float(number) for number in vec]
        if map_file is not None:
            try:
                _write_store(Path(map_file), stored)
            except OSError:
                pass
    if canonical:
        with _LOCK:
            _MEMORY[canonical] = dict(stored)
    return stored


def _read_store(map_file: Path) -> dict[str, list[float]]:
    try:
        raw = json.loads(vectors_path(map_file).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict) or raw.get("model") != EMBED_MODEL:
        return {}
    found = raw.get("vectors")
    if not isinstance(found, dict):
        return {}
    out: dict[str, list[float]] = {}
    for key, vec in found.items():
        if isinstance(key, str) and _valid(vec):
            out[key] = [float(number) for number in vec]
    return out


def _write_store(map_file: Path, vectors: dict[str, list[float]]) -> None:
    target = vectors_path(map_file)
    payload = {
        "model": EMBED_MODEL,
        "vectors": {key: vectors[key] for key in sorted(vectors)},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(raw, encoding="utf-8")
    os.replace(temporary, target)


def _best(
    table: dict[str, str],
    vectors: dict[str, list[float]],
    query_vec: object,
    bar: float,
) -> str | None:
    if not _valid(query_vec):
        return None
    best_key = None
    best_score = -2.0
    for key in sorted(vectors):
        score = cosine(query_vec, vectors[key])
        if score > best_score:
            best_score = score
            best_key = key
    if best_key is None or best_score < bar:
        return None
    answer = table.get(best_key)
    if not isinstance(answer, str) or not answer:
        return None
    return answer
