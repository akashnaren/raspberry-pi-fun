from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from pair.flywheel.miss_queue import node_role
from pair.core import runtime
from pair.routes.base import safe_write

# Base64 of a 4 MB upload is about 5.6 MB. This is the extract body only.
_EXTRACT_BODY_CAP = 6 * 1024 * 1024


def _forward_tool(path: str, payload: dict) -> tuple[int, dict] | None:
    """Send a render call to pi2 or pi3. Off unless PI_PAIR_TOOL_FORWARD=1."""
    if os.environ.get("PI_PAIR_TOOL_FORWARD") != "1":
        return None
    from pair.mesh.tools import Dispatcher, ToolError, registry

    names = {"/tools/render_doc": "render_doc", "/tools/render_chart": "render_chart"}
    tool_name = names.get(path)
    if tool_name is None or tool_name not in registry():
        return None
    peers = {
        str(peer.get("name") or ""): peer
        for peer in runtime.PEERS
        if str(peer.get("name") or "") in ("pi2", "pi3")
    }
    if not peers:
        return None

    def invoke(node: str, tool, body: dict) -> dict:
        peer = peers.get(node)
        if not peer:
            raise ToolError(node)
        url = f"http://{peer['host']}:{int(peer['port'])}{tool.path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=tool.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        return 200, Dispatcher(invoke).call(tool_name, payload)
    except (
        ToolError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
    ):
        return None


class ToolRoutes:
    def _tool(self, path: str) -> None:
        """pi2 and pi3 tools. pi4 may extract only as a last resort. No generation."""
        from pair.nodes.worker import GENERATION_PATHS, handle

        if path in GENERATION_PATHS:
            self._error("this node does not generate", status=404)
            return
        role = node_role()
        render = path in ("/tools/render_doc", "/tools/render_chart")
        if role == "brain" and path != "/tools/extract" and not render:
            self._error("tools run on pi2 or pi3", status=403)
            return
        if path == "/tools/extract":
            row = self._read_json(
                cap=_EXTRACT_BODY_CAP,
                label="tool body",
                oversize="attachment is over 4 MB",
                empty_ok=True,
            )
        else:
            row = self._read_json(label="tool body", empty_ok=True)
        if row is None:
            return
        payload_in = row if isinstance(row, dict) else {}
        if path == "/tools/extract" and str(payload_in.get("content_type") or ""):
            status, payload = self._run_extract(payload_in)
        elif role == "brain" and render:
            status, payload = self._render_tool(path, payload_in)
        else:
            status, payload = handle(path, payload_in)
        body = json.dumps(payload).encode()
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _render_tool(self, path: str, payload: dict) -> tuple[int, dict]:
        """Prefer pi2/pi3 when forwarding is on. Otherwise render here. Never a model call."""
        from pair.nodes.worker import handle

        forwarded = _forward_tool(path, payload)
        if forwarded is not None:
            return forwarded
        return handle(path, payload)
