#!/usr/bin/env python3
"""Pick a paraphrase threshold with no negative merges. Run on pi3.

Reads the canned map, the held-out eval inputs, and tag-disjoint pairs from
the seed. Calls this board's /tools/embed. Writes configs/runtime/embed_pi3.json.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.flywheel.canned import load_map, normalize_key
from pair.core.config import data_root
from pair.flywheel.lifecycle import _heldout_keys, train_config_path
from pair.nodes.embedder import cosine
from pair.flywheel.registry import require_registered
from pair.core.yaml_lite import load_path

_EMBED_URL = "http://127.0.0.1:18080/tools/embed"
_BATCH = 16


def choose_threshold(scores: list[float]) -> float:
    """Lowest hundredth at or above 0.85 with no negative score at or above it.

    1.01 disables paraphrase merges when every cut still accepts a negative.
    """
    for step in range(85, 100):
        cut = step / 100
        if all(score < cut for score in scores):
            return cut
    return 1.01


def _negative_pairs(rows: list[dict], limit: int = 200) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for index, left in enumerate(rows):
        left_tags = {str(tag) for tag in (left.get("tags") or [])}
        left_key = normalize_key(str(left.get("input") or ""))
        if not left_key or not left_tags:
            continue
        for right in rows[index + 1 :]:
            right_tags = {str(tag) for tag in (right.get("tags") or [])}
            right_key = normalize_key(str(right.get("input") or ""))
            if not right_key or not right_tags or not left_tags.isdisjoint(right_tags):
                continue
            pairs.append((left_key, right_key))
            if len(pairs) >= limit:
                return pairs
    return pairs


def _embed(texts: list[str]) -> tuple[str, list[list[float]]]:
    model = ""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH):
        chunk = texts[start : start + _BATCH]
        request = urllib.request.Request(
            _EMBED_URL,
            data=json.dumps({"texts": chunk}).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3.5) as response:
            payload = json.loads(response.read().decode() or "{}")
        if not isinstance(payload, dict) or payload.get("skipped"):
            reason = payload.get("skipped") if isinstance(payload, dict) else "error"
            raise RuntimeError(f"embed skipped: {reason}")
        rows = payload.get("vectors")
        if not isinstance(rows, list) or len(rows) != len(chunk):
            raise RuntimeError("embed returned the wrong number of vectors")
        got = str(payload.get("model") or "")
        if model and got != model:
            raise RuntimeError("embed model changed during calibration")
        model = got
        vectors.extend(rows)
    return model, vectors


def _distribution(scores: list[float]) -> dict:
    if not scores:
        return {"n": 0}
    ordered = sorted(scores)

    def pick(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(fraction * (len(ordered) - 1))))
        return round(ordered[index], 4)

    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p50": pick(0.5),
        "p95": pick(0.95),
        "max": round(ordered[-1], 4),
    }


def main() -> int:
    root = data_root()
    cfg = load_path(train_config_path())
    eval_name = cfg.get("eval")
    if not eval_name:
        print("train config missing eval dataset", file=sys.stderr)
        return 1
    resolved = require_registered([eval_name], root)
    table = load_map(root / "canned" / "canned_map.json")
    heldout = sorted(_heldout_keys(resolved[eval_name]))
    seed_path = root / "canned" / "canned_seed.jsonl"
    seed_rows = []
    for line in seed_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            seed_rows.append(json.loads(line))
    negatives = _negative_pairs(seed_rows)
    texts: list[str] = []
    for key in [*table.keys(), *heldout]:
        if key and key not in texts:
            texts.append(key)
    for left, right in negatives:
        for key in (left, right):
            if key not in texts:
                texts.append(key)
    try:
        model, rows = _embed(texts)
    except (
        OSError,
        urllib.error.URLError,
        TimeoutError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"calibration embed failed: {exc}", file=sys.stderr)
        return 1
    by_text = dict(zip(texts, rows))
    negative_scores = [
        cosine(by_text[left], by_text[right])
        for left, right in negatives
        if left in by_text and right in by_text
    ]
    threshold = choose_threshold(negative_scores)
    out = {
        "paraphrase_min": threshold,
        "model": model,
        "n": len(negative_scores),
        "negative": _distribution(negative_scores),
    }
    dest = ROOT / "configs" / "runtime" / "embed_pi3.json"
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
