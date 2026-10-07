from __future__ import annotations

import json
import time

from pair.core import runtime
from pair.core.thermal import sample as thermal_sample
from pair.flywheel.miss_queue import node_role
from pair.mesh.health import board_thermal, snapshot_peers
from pair.model.modes import mode_table
from pair.routes.base import safe_write


def listed_chat_models() -> list[str] | None:
    """Tags Ollama reported for pi4. None when the board is down or the list is empty."""
    for snap in snapshot_peers():
        if snap.get("name") != "pi4" or not snap.get("ok"):
            continue
        names = [str(item) for item in (snap.get("models") or []) if item]
        return names or None
    return None


def mode_fields(mode: str, route: str, resident: str = "") -> dict:
    """Route fields for the page. Residency is never a swap plan."""
    del resident
    extra: dict[str, str] = {}
    if mode:
        extra["pi_mode"] = mode
    if route:
        extra["pi_route"] = route
    return extra


_BOOTED = time.monotonic()


def _service_row(peers: list, role: str, name: str) -> dict:
    match = None
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        if peer.get("role") == role or peer.get("name") == name:
            match = peer
            break
    if match is None:
        return {"name": name, "ok": False, "latency_ms": None}
    return {
        "name": name,
        "ok": bool(match.get("ok")),
        "latency_ms": match.get("latency_ms"),
    }


def _claim_wait(slot: dict, cancel=None, on_tick=None) -> bool:
    """Turn a queue reservation into a generation slot. False means the wait ran out."""
    slot["waiting"] = False
    if runtime.gate.acquire_reserved(cancel=cancel, on_tick=on_tick):
        slot["held"] = True
        return True
    return False


def health_document() -> dict:
    peers = snapshot_peers()
    up = sum(1 for peer in peers if isinstance(peer, dict) and peer.get("ok"))
    table = mode_table()
    pro_model = str(table.get("pro") or "").strip()
    doc = {
        "ok": True,
        "model": runtime.MODEL,
        "pro_model": pro_model,
        "mode": "flash",
        "modes": table,
        "peers_up": up,
        "peers": peers,
        "slots": runtime.INFER_SLOTS,
        "in_flight": runtime.gate.in_flight(),
        "waiting": runtime.gate.waiting(),
        "cache_ttl": runtime.HEALTH_CACHE_TTL,
        "uptime_s": max(0, int(time.monotonic() - _BOOTED)),
        "memory": _memory_stats(),
        "services": {
            "brain": _service_row(peers, "brain", "pi4"),
            "search": _service_row(peers, "health", "pi2"),
            "peers_up": up,
            "peers": len(peers),
        },
    }
    doc.update(board_thermal())
    temp = thermal_sample().get("temp_c")
    if temp is not None:
        doc["temp_c"] = temp
    if node_role() == "dataset":
        from pair.nodes import embedder

        embed_state = embedder.state()
        doc["services"]["embed"] = {
            "ok": embed_state in {"unloaded", "loaded", "ocr_busy"},
            "state": embed_state,
        }
    return doc


def _memory_stats() -> dict:
    try:
        from pair.memory import store as memory

        return memory.stats("")
    except Exception:
        return {"used": 0, "num_ctx": 0, "compactions": 0, "last_compact_ms": 0}


def public_health(doc: dict) -> dict:
    """What a tunnel may show. Names, hosts, ports, roles, and latency stay off."""
    services: dict = {}
    for key, value in (doc.get("services") or {}).items():
        if isinstance(value, dict) and "ok" in value:
            services[key] = bool(value.get("ok"))
        elif isinstance(value, bool):
            services[key] = value
    peers = []
    for peer in doc.get("peers") or []:
        if not isinstance(peer, dict):
            continue
        peers.append({"ok": bool(peer.get("ok"))})
    shown = {
        "ok": bool(doc.get("ok")),
        "slots": doc.get("slots"),
        "in_flight": doc.get("in_flight"),
        "waiting": doc.get("waiting", runtime.gate.waiting()),
        "uptime_s": doc.get("uptime_s"),
        "peers_up": doc.get("peers_up"),
        "services": services,
        "peers": peers,
    }
    return shown


class StatusRoutes:
    def _tool_health(self) -> None:
        from pair.nodes.worker import health_body

        body = json.dumps(health_body()).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _warming(self, want_stream: bool, message: str, started: float) -> None:
        """A cold Flash tag is a wait, not a 502 and not a larger-model error."""
        if want_stream:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            chunk = {
                "id": "pi-pair",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"content": message}, "finish_reason": None}
                ],
                "pi_detail": message,
            }
            safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
            return
        body = json.dumps(
            {
                "id": "pi-pair",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": message},
                        "finish_reason": "stop",
                    }
                ],
                "pi_detail": message,
                "pi_ms": int((time.time() - started) * 1000),
            }
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)
