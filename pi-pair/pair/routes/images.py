from __future__ import annotations
import json
import threading
import time
from collections import deque
from pair.core import runtime
from pair.routes.base import safe_write


_IMAGE_BODY_CAP = 8192
_IMAGE_QUESTION_CAP = 500
_IMAGE_ANSWER_CAP = 4000
_IMAGE_SOURCE_CAP = 8
_IMAGE_CONNECT_S = 0.6
_IMAGE_READ_S = 5.0
_IMAGE_GLOBAL_MAX = 4
_IMAGE_PER_MIN = 20
_image_limit_lock = threading.Lock()
_image_global = 0
_image_inflight: dict[str, int] = {}
_image_hits: dict[str, deque] = {}


def reset_image_admission() -> None:
    """Clear the public image-route limits. Tests call this between cases."""
    global _image_global
    with _image_limit_lock:
        _image_global = 0
        _image_inflight.clear()
        _image_hits.clear()


def _image_admit(key: str) -> bool:
    """One in flight per client, twenty a minute, four in flight on this process."""
    global _image_global
    now = time.monotonic()
    with _image_limit_lock:
        hits = _image_hits.setdefault(key, deque())
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if (
            _image_inflight.get(key, 0) >= 1
            or _image_global >= _IMAGE_GLOBAL_MAX
            or len(hits) >= _IMAGE_PER_MIN
        ):
            return False
        hits.append(now)
        _image_inflight[key] = _image_inflight.get(key, 0) + 1
        _image_global += 1
        return True


def _image_release(key: str) -> None:
    global _image_global
    with _image_limit_lock:
        _image_global = max(0, _image_global - 1)
        left = _image_inflight.get(key, 0) - 1
        if left <= 0:
            _image_inflight.pop(key, None)
        else:
            _image_inflight[key] = left


def _image_load(_names):
    """pi2 first. pi3 runs only when pi2 errors or its breaker is open."""
    return {
        "pi2": {"load": 0, "queue": 0, "temp_c": 0},
        "pi3": {"load": 1, "queue": 0, "temp_c": 0},
    }


def _post_image_tool(peer: dict, path: str, payload: dict) -> dict:
    """POST one tool host. Connect stays short. Redirects are not followed."""
    import http.client

    from pair.mesh.tools import ToolError

    host = str(peer.get("host") or "")
    try:
        port = int(peer["port"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ToolError("peer") from exc
    body = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection(host, port, timeout=_IMAGE_CONNECT_S)
    try:
        conn.connect()
        if conn.sock is not None:
            conn.sock.settimeout(_IMAGE_READ_S)
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "content-type": "application/json",
                "content-length": str(len(body)),
            },
        )
        response = conn.getresponse()
        raw = response.read(65536)
        status = response.status
    finally:
        conn.close()
    if status != 200:
        raise ToolError(f"status {status}")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ToolError("bad result")
    return data


def _image_forward(payload: dict) -> list:
    """pi2, then pi3. No local lookup on pi4."""
    from pair.mesh.tools import Dispatcher, registry

    if "images" not in registry():
        return []
    peers = {
        str(peer.get("name") or ""): peer
        for peer in runtime.PEERS
        if str(peer.get("name") or "") in ("pi2", "pi3")
    }
    if not peers:
        return []

    def invoke(node: str, tool, body: dict) -> dict:
        from pair.mesh.tools import ToolError

        peer = peers.get(node)
        if not peer:
            raise ToolError(node)
        return _post_image_tool(peer, tool.path, body)

    try:
        result = Dispatcher(invoke, hedge_s=5.0, load=_image_load).call(
            "images", payload
        )
    except Exception:
        return []
    rows = result.get("cards") if isinstance(result, dict) else None
    return rows if isinstance(rows, list) else []


class ImageRoutes:
    def _image_json(self, cards: list) -> None:
        from pair.render.images import sanitize_card

        clean = []
        for item in cards:
            card = sanitize_card(item)
            if not card:
                continue
            if any(have.get("url") == card.get("url") for have in clean):
                continue
            clean.append(card)
            if len(clean) >= 4:
                break
        body = json.dumps({"pi_images": clean}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _images(self) -> None:
        """Forward a finished turn to pi2. Never reserves the decode slot."""
        row = self._read_json(
            cap=_IMAGE_BODY_CAP,
            label="image request",
            oversize="That image request is over the cap.",
            empty_ok=False,
        )
        if row is None:
            return
        question = row.get("question", "")
        answer = row.get("answer", "")
        sources = row.get("sources", [])
        if (
            not isinstance(question, str)
            or not isinstance(answer, str)
            or not isinstance(sources, list)
            or len(question) > _IMAGE_QUESTION_CAP
            or len(answer) > _IMAGE_ANSWER_CAP
            or len(sources) > _IMAGE_SOURCE_CAP
        ):
            self._error("That image request is over the cap.", status=400)
            return
        key = self._client_key() or "-"
        if not _image_admit(key):
            self._image_json([])
            return
        try:
            found = _image_forward(
                {
                    "question": question,
                    "answer": answer,
                    "sources": sources[:_IMAGE_SOURCE_CAP],
                }
            )
        except Exception:
            found = []
        finally:
            _image_release(key)
        try:
            self._image_json(found)
        except Exception:
            return
