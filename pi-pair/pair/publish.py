"""Public label sync for pi3. Hashes and votes only. Tokens stay in the environment."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

from pair.config import data_root
from pair.mesh import DEFAULT_DATASET, mesh_config

_LOCK = threading.Lock()
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,40}/[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9: _./-]{1,80}$")
HF_ENDPOINT = "https://huggingface.co"
HF_TIMEOUT = 20.0
PUBLIC_BOUND = 128
_CARD = """---
license: other
language:
- en
tags:
- labels
- raspberry-pi
pretty_name: Pi mesh labels
---

# pi-mesh-labels

Public votes from the OpenPi mesh. Each row is a vote (`up` or `down`) and HMAC-SHA256 of the prompt, the answer, and an optional correction. The key is `PI_PAIR_LABEL_PEPPER` on the dataset host. It is not a bare SHA-256 of the text.

Raw chat is not in this dataset. The upload token is `HF_TOKEN` on the dataset host. Leave it unset until a public dataset is approved. Neither value is in this repo.
"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HF_OPENER = urllib.request.build_opener(_NoRedirect)


def dataset_id() -> str:
    raw = os.environ.get("HF_DATASET_ID", "").strip() or str(
        mesh_config().get("hf_dataset") or DEFAULT_DATASET
    )
    if not _REPO.fullmatch(raw):
        raise ValueError("HF dataset id is not a public repo id")
    return raw


def public_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "train" / "public" / "labels.jsonl"


def label_pepper() -> bytes | None:
    """32-byte key from PI_PAIR_LABEL_PEPPER. Hex (64 digits) or raw text of at least 32 bytes."""
    text = os.environ.get("PI_PAIR_LABEL_PEPPER", "").strip()
    if not text:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return bytes.fromhex(text)
    raw = text.encode("utf-8")
    if len(raw) < 32:
        return None
    return raw


def _content_hmac(text: str) -> str | None:
    pepper = label_pepper()
    if pepper is None:
        return None
    return hmac.new(pepper, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _keep_public(row: dict) -> dict | None:
    vote = str(row.get("vote") or "").strip().lower()
    prompt_hash = str(row.get("prompt_sha256") or "")
    answer_hash = str(row.get("answer_sha256") or "")
    if vote not in ("up", "down"):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", prompt_hash):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", answer_hash):
        return None
    public = {
        "prompt_sha256": prompt_hash,
        "answer_sha256": answer_hash,
        "vote": vote,
        "redacted": True,
    }
    correction = str(row.get("correction_sha256") or "")
    if correction:
        if not re.fullmatch(r"[0-9a-f]{64}", correction):
            return None
        public["correction_sha256"] = correction
    for key in ("chip", "peer"):
        value = str(row.get(key) or "")
        if value and _SAFE_LABEL.fullmatch(value):
            public[key] = value
    return public


def redact_label(row: dict) -> dict | None:
    """Vote plus hashes. Raw prompt, answer, and correction are not copied."""
    if not isinstance(row, dict):
        return None
    if row.get("redacted") is True and "prompt" not in row and "answer" not in row:
        return _keep_public(row)
    vote = str(row.get("vote") or "").strip().lower()
    if vote not in ("up", "down"):
        return None
    prompt = str(row.get("prompt") or "").strip()
    answer = str(row.get("answer") or "").strip()
    if not prompt or not answer:
        return None
    prompt_hash = _content_hmac(prompt)
    answer_hash = _content_hmac(answer)
    if not prompt_hash or not answer_hash:
        return None
    public = {
        "prompt_sha256": prompt_hash,
        "answer_sha256": answer_hash,
        "vote": vote,
        "redacted": True,
    }
    correction = str(row.get("correction") or "").strip()
    if correction:
        correction_hash = _content_hmac(correction)
        if not correction_hash:
            return None
        public["correction_sha256"] = correction_hash
    for key in ("chip", "peer"):
        value = str(row.get(key) or "").strip()
        if value and _SAFE_LABEL.fullmatch(value):
            public[key] = value
    return public


def _row_key(row: dict) -> tuple:
    return (
        row.get("prompt_sha256"),
        row.get("answer_sha256"),
        row.get("vote"),
        row.get("correction_sha256") or "",
    )


def _read_public(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        clean = _keep_public(item) if isinstance(item, dict) else None
        if clean:
            rows.append(clean)
    return rows


def record_public_labels(rows: list[dict], root: Path | None = None) -> list[dict]:
    """Append hashed votes. Raw chat is not written. Existing hashes stay."""
    path = public_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        existing = _read_public(path)
        seen = {_row_key(row) for row in existing}
        for row in rows or []:
            clean = redact_label(row)
            if not clean:
                continue
            key = _row_key(clean)
            if key in seen:
                continue
            seen.add(key)
            existing.append(clean)
        if len(existing) > PUBLIC_BOUND:
            existing = existing[-PUBLIC_BOUND:]
        body = "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in existing)
        path.write_text(body, encoding="utf-8")
        return list(existing)


def votes_in_queue(root: Path | None = None) -> list[dict]:
    path = (root or data_root()) / "train" / "pending" / "queue.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and str(item.get("vote") or "").lower() in (
            "up",
            "down",
        ):
            rows.append(item)
    return rows


def _scrub(text: str, token: str) -> str:
    cleaned = text or ""
    if token:
        cleaned = cleaned.replace(token, "[redacted]")
    return " ".join(cleaned.split())[:160]


def _hf_post(
    url: str, body: bytes, token: str, content_type: str, opener, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": content_type,
            "user-agent": "pi-mesh-labels",
        },
        method="POST",
    )
    open_url = opener or _HF_OPENER.open
    try:
        with open_url(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or getattr(response, "code", 200)
            return int(status), response.read(262144)
    except urllib.error.HTTPError as error:
        raw = error.read(262144) if getattr(error, "fp", None) else b""
        return int(error.code), raw


def _hf_put(
    url: str, body: bytes, token: str, opener, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": "pi-mesh-labels",
        },
        method="PUT",
    )
    open_url = opener or _HF_OPENER.open
    try:
        with open_url(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or getattr(response, "code", 200)
            return int(status), response.read(65536)
    except urllib.error.HTTPError as error:
        raw = error.read(65536) if getattr(error, "fp", None) else b""
        return int(error.code), raw


def _public_rows(rows: list[dict] | None) -> list[dict]:
    clean = []
    seen = set()
    for row in rows or []:
        item = redact_label(row)
        if not item:
            continue
        key = _row_key(item)
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
    return clean


def sync_huggingface(
    rows: list[dict] | None, opener=None, timeout: float = HF_TIMEOUT
) -> dict:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        return {"status": "skipped", "reason": "HF_TOKEN unset"}
    public = _public_rows(rows)
    if not public:
        return {"status": "skipped", "reason": "no labels"}
    try:
        repo = dataset_id()
    except ValueError as error:
        return {"status": "error", "reason": _scrub(str(error), token)}
    name = repo.split("/", 1)[1]
    try:
        status, _raw = _hf_post(
            f"{HF_ENDPOINT}/api/repos/create",
            json.dumps({"type": "dataset", "name": name, "private": False}).encode(),
            token,
            "application/json",
            opener,
            timeout,
        )
        if status not in (200, 201, 409):
            return {"status": "error", "reason": f"huggingface create http {status}"}
        status, _raw = _hf_put(
            f"{HF_ENDPOINT}/api/datasets/{repo}/settings",
            json.dumps({"private": False}).encode(),
            token,
            opener,
            timeout,
        )
        if status not in (200, 201, 204):
            return {
                "status": "error",
                "reason": f"huggingface visibility http {status}",
            }
        jsonl = "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in public)
        commit = "".join(
            json.dumps(item, ensure_ascii=True) + "\n"
            for item in (
                {
                    "key": "header",
                    "value": {
                        "summary": "Sync public mesh labels",
                        "description": "Votes and HMAC-SHA256. No raw chat.",
                    },
                },
                {
                    "key": "file",
                    "value": {
                        "path": "README.md",
                        "content": base64.b64encode(_CARD.encode()).decode(),
                        "encoding": "base64",
                    },
                },
                {
                    "key": "file",
                    "value": {
                        "path": "labels.jsonl",
                        "content": base64.b64encode(jsonl.encode()).decode(),
                        "encoding": "base64",
                    },
                },
            )
        )
        status, _raw = _hf_post(
            f"{HF_ENDPOINT}/api/datasets/{repo}/commit/main",
            commit.encode(),
            token,
            "application/x-ndjson",
            opener,
            timeout,
        )
    except Exception as error:
        return {
            "status": "error",
            "reason": _scrub(f"{error.__class__.__name__}", token),
        }
    if status not in (200, 201):
        return {"status": "error", "reason": f"huggingface commit http {status}"}
    return {"status": "ok", "repo": repo, "rows": len(public), "private": False}


def sync_kaggle(rows: list[dict] | None) -> dict:
    """Optional stub behind Hugging Face. Does not upload and does not echo the token."""
    token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    if not token:
        return {"status": "skipped", "reason": "KAGGLE_API_TOKEN unset"}
    public = _public_rows(rows)
    return {
        "status": "stub",
        "rows": len(public),
        "dataset": DEFAULT_DATASET,
        "reason": "optional; huggingface is the public copy",
    }


def sync_public_labels(
    rows: list[dict] | None = None, *, opener=None, root: Path | None = None
) -> dict:
    """Hugging Face first. Kaggle runs only after that upload succeeds."""
    public = list(rows) if rows is not None else _read_public(public_path(root))
    hf = sync_huggingface(public, opener=opener)
    result = {
        "huggingface": hf,
        "kaggle": {"status": "skipped", "reason": "behind huggingface"},
    }
    if hf.get("status") == "ok":
        result["kaggle"] = sync_kaggle(public)
    return result


def sync_from_disk(root: Path | None = None, opener=None) -> dict:
    base = root or data_root()
    recorded = record_public_labels(votes_in_queue(base), base)
    return sync_public_labels(recorded, opener=opener, root=base)


def schedule_public_sync(rows: list[dict], root: Path | None = None) -> None:
    """Background upload so a vote response does not wait on the hub."""
    if not os.environ.get("HF_TOKEN", "").strip():
        return
    if not rows:
        return

    def run() -> None:
        try:
            sync_public_labels(rows, root=root)
        except Exception:
            return

    threading.Thread(target=run, daemon=True).start()
