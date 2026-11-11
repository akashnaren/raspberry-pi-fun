#!/usr/bin/env python3
"""OpenPi nightly downtime banner: serves banner.html for every path with 503 + Retry-After."""

import datetime
import os
import zoneinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
with open(os.path.join(HERE, "banner.html"), "rb") as f:
    BODY = f.read()


def retry_after():
    now = datetime.datetime.now(TZ)
    tgt = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if now >= tgt:
        tgt = (now + datetime.timedelta(days=1)).replace(
            hour=7, minute=0, second=0, microsecond=0
        )
    return max(30, int((tgt - now).total_seconds()))


class H(BaseHTTPRequestHandler):
    server_version = "OpenPiBanner"
    sys_version = ""

    def _send(self, body=True):
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("Retry-After", str(retry_after()))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-OpenPi-Downtime", "1")
        self.end_headers()
        if body:
            self.wfile.write(BODY)

    def do_GET(self):
        self._send()

    def do_POST(self):
        self._send()

    def do_HEAD(self):
        self._send(body=False)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(
        ("127.0.0.1", int(os.environ.get("BANNER_PORT", "18080"))), H
    ).serve_forever()
