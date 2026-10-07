from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from pair.core.errors import friendly_body
from pair.mesh.guard import PI4_MISS_DOWN, may_generate
from pair.core import runtime
from pair.routes.base import safe_write
from pair.routes.chat import apply_tier


def brain_chat_url() -> str:
    """pi4's page, not its Ollama port. Search and decode both happen there."""
    peer = next((item for item in runtime.PEERS if item.get("name") == "pi4"), None)
    if peer is None or not may_generate(peer):
        raise RuntimeError(PI4_MISS_DOWN)
    port = int(os.environ.get("PI_PAIR_BRAIN_PORT", "18080"))
    return f"http://{peer['host']}:{port}/v1/chat/completions"


def relay_chat(
    payload: bytes, target: str, mesh: str, mode: str = ""
) -> tuple[int, dict[str, str], bytes]:
    """Hand the chat to pi4. This relay does not search and does not generate."""
    headers = {
        "content-type": "application/json",
        "X-Pi-Target": target or "auto",
        "X-Pi-Mesh": mesh or "on",
    }
    if mode:
        headers["X-Pi-Mode"] = mode
    request = urllib.request.Request(
        brain_chat_url(),
        data=payload,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            headers = {
                "content-type": response.headers.get(
                    "content-type", "application/json"
                ),
            }
            for name in (
                "X-Pi-Peer",
                "X-Pi-Chip",
                "X-Pi-Think",
                "X-Pi-Search",
                "X-Pi-Mode",
                "X-Pi-Route",
            ):
                value = response.headers.get(name)
                if value:
                    headers[name] = value
            return response.status, headers, raw
    except urllib.error.HTTPError as error:
        raw = friendly_body(error.read())
        return error.code, {"content-type": "application/json"}, raw
    except Exception:
        raise RuntimeError(PI4_MISS_DOWN) from None


class RelayRoutes:
    def _relay_to_brain(self, payload: bytes) -> None:
        try:
            requested = json.loads(payload.decode() or "{}")
        except json.JSONDecodeError:
            requested = {}
        if isinstance(requested, dict) and requested.get("stream"):
            self._relay_stream(payload)
            return
        target = (self.headers.get("X-Pi-Target") or "auto").strip()
        mesh = (self.headers.get("X-Pi-Mesh") or "on").strip()
        try:
            status, headers, body = relay_chat(
                payload, target, mesh, getattr(self, "pi_mode", "") or ""
            )
        except RuntimeError as error:
            self._error(str(error))
            return
        mode = getattr(self, "public_mode", "") or ""
        kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if mode and kind != "text/event-stream":
            try:
                parsed = json.loads(body.decode() or "{}")
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                apply_tier(self, parsed)
                body = json.dumps(parsed).encode()
                headers["content-type"] = "application/json"
                headers.pop("content-length", None)
        self.send_response(status)
        self._cors()
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _relay_stream(self, payload: bytes) -> None:
        """Pass pi4's event stream through. This relay does not search or generate."""
        target = (self.headers.get("X-Pi-Target") or "auto").strip()
        mesh = (self.headers.get("X-Pi-Mesh") or "on").strip()
        forward = {
            "content-type": "application/json",
            "X-Pi-Target": target or "auto",
            "X-Pi-Mesh": mesh or "on",
        }
        tier = getattr(self, "pi_mode", "") or ""
        if tier:
            forward["X-Pi-Mode"] = tier
        request = urllib.request.Request(
            brain_chat_url(),
            data=payload,
            headers=forward,
        )
        try:
            response = urllib.request.urlopen(request, timeout=180)
        except urllib.error.HTTPError as error:
            raw = friendly_body(error.read())
            self.send_response(error.code)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            safe_write(self, raw)
            return
        except Exception:
            self._error(PI4_MISS_DOWN)
            return
        try:
            self.send_response(response.status)
            self._cors()
            self.send_header(
                "content-type",
                response.headers.get("content-type", "text/event-stream"),
            )
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            for name in (
                "X-Pi-Peer",
                "X-Pi-Chip",
                "X-Pi-Think",
                "X-Pi-Search",
                "X-Pi-Mode",
                "X-Pi-Route",
            ):
                value = response.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.end_headers()
            while True:
                chunk = response.read(1024)
                if not chunk:
                    break
                if not safe_write(self, chunk, flush=True):
                    break
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
