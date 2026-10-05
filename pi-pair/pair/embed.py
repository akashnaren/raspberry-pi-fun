"""Pi4 paraphrase match for the canned map. Stdlib only.

Exact keys stay in canned.lookup. This client runs only on the brain role,
which is pi4. After the socket is listening, a background thread embeds the
map keys once. A later miss posts only the normalized line to Ollama
`/api/embed` with `snowflake-arctic-embed:m`. While that batch is still
running and the key cache is empty, a miss does not embed the keys again.
If the preload failed, or the map keys changed, the miss embeds the keys
and then the line. A cosine at or above COSINE_MIN returns the stored answer.
A down embedder, a bad payload, or a weaker score is a miss, and the chat
path still runs.
"""

from __future__ import annotations

import json
import math
import os
import threading
import urllib.request

from pair.http_pool import open_json_request
from pair.knobs import keep_alive

EMBED_MODEL = "snowflake-arctic-embed:m"
COSINE_MIN = 0.85
EMBED_TIMEOUT_S = 30.0

_LOCK = threading.Lock()
_CACHE: dict = {"sig": None, "vectors": None}
_QUERY: dict[str, list[float]] = {}
_QUERY_MAX = 64
_WARM_LOCK = threading.Lock()
_WARM = {"status": "ready"}


def on_pi4() -> bool:
    """True only for the brain role. That role is pi4; other boards stay exact-key."""
    role = os.environ.get("PI_PAIR_ROLE", "").strip().lower()
    if role in ("brain", "health", "dataset"):
        return role == "brain"
    name = os.environ.get("PI_PAIR_NAME", "").strip().lower()
    return "pi4" in name


def ollama_base() -> str:
    """Origin of the local Ollama that holds the embed model. Not the chat peer."""
    raw = os.environ.get("PI_PAIR_OLLAMA", "").strip()
    if not raw:
        raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    if raw.startswith("http://") or raw.startswith("https://"):
        base = raw
    else:
        base = "http://" + raw
    # 0.0.0.0 is a bind address. The client has to use the loopback.
    base = base.replace("://0.0.0.0", "://127.0.0.1", 1)
    return base.rstrip("/")


def embed_url() -> str:
    base = ollama_base()
    if base.endswith("/api/embed"):
        return base
    return base + "/api/embed"


def reset_embed_cache() -> None:
    with _LOCK:
        _CACHE["sig"] = None
        _CACHE["vectors"] = None
        _QUERY.clear()


def warm_status() -> str:
    """`warming` while the key batch is in flight, otherwise `ready`.

    Reads a lock and returns. It does not join the preload thread.
    """
    with _WARM_LOCK:
        return str(_WARM["status"])


def set_warm_status(status: str) -> None:
    if status not in ("warming", "ready"):
        raise ValueError(f"unknown warm status {status}")
    with _WARM_LOCK:
        _WARM["status"] = status


def reset_warm_state() -> None:
    set_warm_status("ready")


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_sq = 0.0
    right_sq = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_sq += a * a
        right_sq += b * b
    if left_sq <= 0.0 or right_sq <= 0.0:
        return 0.0
    return dot / math.sqrt(left_sq * right_sq)


def _as_vector(value) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        out.append(float(item))
    return out


def _parse_embeddings(body, count: int) -> list[list[float]] | None:
    if not isinstance(body, dict):
        return None
    raw = body.get("embeddings")
    if not isinstance(raw, list) or len(raw) != count:
        return None
    vectors: list[list[float]] = []
    for item in raw:
        vector = _as_vector(item)
        if vector is None:
            return None
        vectors.append(vector)
    return vectors


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """POST `/api/embed`. None means the caller should treat the line as a miss."""
    if not texts:
        return []
    payload = {
        "model": EMBED_MODEL,
        "input": list(texts),
        "truncate": True,
        "keep_alive": keep_alive(),
    }
    request = urllib.request.Request(
        embed_url(),
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with open_json_request(request, EMBED_TIMEOUT_S) as response:
            body = json.loads(response.read().decode())
    except Exception:
        return None
    return _parse_embeddings(body, len(texts))


def _cached_key_vectors(keys: list[str]) -> dict[str, list[float]] | None:
    sig = tuple(keys)
    with _LOCK:
        cached = _CACHE["vectors"]
        if _CACHE["sig"] == sig and isinstance(cached, dict):
            return cached
    return None


def _key_vectors(keys: list[str]) -> dict[str, list[float]] | None:
    cached = _cached_key_vectors(keys)
    if cached is not None:
        return cached
    sig = tuple(keys)
    embedded = embed_texts(list(sig))
    if embedded is None:
        return None
    mapped = {key: vector for key, vector in zip(sig, embedded)}
    with _LOCK:
        _CACHE["sig"] = sig
        _CACHE["vectors"] = mapped
    return mapped


def warm_canned_embeddings(table: dict[str, str]) -> None:
    """Embed every map key once, before the first chat.

    A cold cache makes the first paraphrase embed the keys and the line.
    On the pi4 that was several seconds. After a successful warm, a miss
    embeds the line only. Off the brain this returns without a request.
    A failed preload is logged and left empty so the lazy fill can retry.
    """
    if not on_pi4():
        return
    try:
        keys = sorted(key for key in table if key)
        if not keys or _cached_key_vectors(keys) is not None:
            return
        if _key_vectors(keys) is None:
            print(
                "canned embed warm missed; the first paraphrase will embed the keys",
                flush=True,
            )
            return
        print(f"canned embed warm: {len(keys)} keys", flush=True)
    except Exception as exc:
        print(f"canned embed warm skipped: {exc}", flush=True)


def _recall_query(text: str) -> list[float] | None:
    with _LOCK:
        found = _QUERY.get(text)
        return list(found) if found is not None else None


def _remember_query(text: str, vector: list[float]) -> None:
    with _LOCK:
        if text in _QUERY:
            return
        if len(_QUERY) >= _QUERY_MAX:
            _QUERY.pop(next(iter(_QUERY)))
        _QUERY[text] = list(vector)


def _best_answer(
    query_vec: list[float], vectors: dict[str, list[float]], table: dict[str, str]
) -> str | None:
    best_key = None
    best_score = 0.0
    for key in sorted(vectors):
        score = cosine(query_vec, vectors[key])
        if score < COSINE_MIN:
            continue
        if best_key is None or score > best_score:
            best_key = key
            best_score = score
    if best_key is None:
        return None
    return table.get(best_key)


def semantic_lookup(query: str, table: dict[str, str]) -> str | None:
    """Highest key at or above COSINE_MIN, or None.

    The user line and the map keys are embedded as the same kind of text.
    A retrieval prefix on only one side would score a paraphrase like a passage.
    While the startup batch is still running and this key cache is empty, this
    returns None. Scoring the line would embed every key again and hold the
    chat. Once those vectors are cached, only the line is embedded.
    """
    try:
        keys = sorted(key for key in table if key)
        if not query or not keys:
            return None
        if warm_status() == "warming" and _cached_key_vectors(keys) is None:
            return None
        vectors = _key_vectors(keys)
        if not vectors:
            return None
        # A warming brain must not score a line from a vector remembered
        # before this batch. The embedder may be down, and a hit would skip
        # the one query the caller still expects.
        query_vec = None if warm_status() == "warming" else _recall_query(query)
        if query_vec is None:
            got = embed_texts([query])
            if not got:
                return None
            query_vec = got[0]
            _remember_query(query, query_vec)
        return _best_answer(query_vec, vectors, table)
    except Exception:
        return None
