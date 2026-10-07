"""HTTP fakes shared by the router tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()


class OllamaFake(BaseHTTPRequestHandler):
    posts = 0
    last_payload = None
    catalog = ["qwen3:0.6b"]

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        names = list(type(self).catalog)
        body = json.dumps({"models": [{"name": name} for name in names]}).encode()
        self._json(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).posts += 1
        type(self).last_payload = payload
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(
                json.dumps({"message": {"content": "hel"}, "done": False}).encode()
                + b"\n"
            )
            self.wfile.write(
                json.dumps(
                    {"message": {"content": "lo from peer"}, "done": True}
                ).encode()
                + b"\n"
            )
            return
        self._json(json.dumps({"message": {"content": "hello from peer"}}).encode())

    def _json(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LlamaFake(BaseHTTPRequestHandler):
    last_payload = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "tiny"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).last_payload = payload
        content = "llama:" + str(payload.get("model") or "")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunk = {
                "choices": [{"delta": {"content": content}, "finish_reason": "stop"}]
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ScriptOllama(OllamaFake):
    replies: list = []
    seen: list = []

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).posts += 1
        type(self).last_payload = payload
        type(self).seen.append(payload)
        reply = (
            type(self).replies.pop(0)
            if type(self).replies
            else {"message": {"content": "x"}, "done": True}
        )
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            chunks = reply.get("chunks") if isinstance(reply, dict) else None
            if isinstance(chunks, list) and chunks:
                last = len(chunks) - 1
                reason = str(reply.get("done_reason") or "stop")
                for index, piece in enumerate(chunks):
                    line = {"message": {"content": piece}, "done": index == last}
                    if index == last:
                        line["done_reason"] = reason
                    self.wfile.write(json.dumps(line).encode() + b"\n")
                    self.wfile.flush()
                return
            self.wfile.write(json.dumps(reply).encode() + b"\n")
            return
        self._json(json.dumps(reply).encode())
