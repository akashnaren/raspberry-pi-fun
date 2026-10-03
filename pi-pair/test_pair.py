"""Stdlib tests for Pi 0.2 High helpers and the chat HTTP contract."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import health, runtime
from pair.chat import llamacpp_model
from pair.config import DEFAULT_PEERS, load_peers, normalize_peer
from pair.peers import model_on_peer, pick
from pair.server import make_server
from pair.stream import llamacpp_delta, ollama_delta


def _composer_keydown(script: str) -> str:
    start = script.index("addEventListener('keydown'")
    end = script.index("document.querySelectorAll('.think-btn')", start)
    return script[start:end]


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()


class OllamaFake(BaseHTTPRequestHandler):
    posts = 0
    last_payload = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"models": [{"name": "qwen2.5:0.5b"}]}).encode()
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
            self.wfile.write(json.dumps({"message": {"content": "hel"}, "done": False}).encode() + b"\n")
            self.wfile.write(json.dumps({"message": {"content": "lo"}, "done": True}).encode() + b"\n")
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
        content = "llama:" + payload.get("model", "")
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
