from __future__ import annotations

import importlib
import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = ROOT / "DEPLOYED_SHA"
PUBLIC_KEYS = {
    "ok",
    "slots",
    "in_flight",
    "waiting",
    "uptime_s",
    "peers_up",
    "services",
    "peers",
    "commit",
}
_PEERS = json.dumps(
    [
        {
            "name": "pi4",
            "host": "127.0.0.1",
            "port": 1,
            "kind": "ollama",
            "note": "10.0.0.181",
            "generative": True,
            "role": "brain",
        }
    ]
)


def _serve(spec: str) -> None:
    if spec == "missing":
        STAMP.unlink(missing_ok=True)
    elif spec == "empty":
        STAMP.write_bytes(b"")
    elif spec.startswith("text:"):
        STAMP.write_text(spec.removeprefix("text:"), encoding="utf-8")
    else:
        raise SystemExit(2)
    server = importlib.import_module("pair.server")
    httpd = server.make_server("127.0.0.1", 0)
    print(httpd.server_address[1], flush=True)
    httpd.serve_forever(poll_interval=0.05)


class HealthCommit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        handle.write(_PEERS)
        handle.close()
        cls.peers = Path(handle.name)

    @classmethod
    def tearDownClass(cls):
        cls.peers.unlink(missing_ok=True)

    @contextmanager
    def _server(self, spec: str):
        saved = STAMP.read_bytes() if STAMP.exists() else None
        err = tempfile.TemporaryFile()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        env["PI_PAIR_PEERS"] = str(self.peers)
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve", spec],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            bufsize=1,
        )
        try:
            yield self._port(proc, err)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if proc.stdout is not None:
                proc.stdout.close()
            err.close()
            if saved is None:
                STAMP.unlink(missing_ok=True)
            else:
                STAMP.write_bytes(saved)

    def _port(self, proc: subprocess.Popen[str], err) -> int:
        assert proc.stdout is not None
        ready, _, _ = select.select([proc.stdout], [], [], 30)
        if not ready or proc.poll() is not None:
            err.seek(0)
            detail = err.read().decode(errors="replace")
            self.fail(detail or "server exited before printing a port")
        line = proc.stdout.readline().strip()
        return int(line)

    def _get(self, port: int, *, public: bool = False) -> tuple[str, dict]:
        command = [
            "curl",
            "-sS",
            "--max-time",
            "20",
            f"http://127.0.0.1:{port}/health",
        ]
        if public:
            command.extend(["-H", "Cf-Ray: x"])
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout, json.loads(done.stdout)

    def test_a_twelve_hex_chars_are_the_commit(self):
        with self._server("text:0123456789ab") as port:
            _raw, body = self._get(port)
        self.assertIn("commit", body)
        self.assertEqual(body["commit"], "0123456789ab")

    def test_b_missing_and_empty_files_are_unknown(self):
        with self._server("missing") as port:
            _raw, missing = self._get(port)
        self.assertIn("commit", missing)
        self.assertEqual(missing["commit"], "unknown")
        with self._server("empty") as port:
            _raw, empty = self._get(port)
        self.assertIn("commit", empty)
        self.assertEqual(empty["commit"], "unknown")

    def test_c_a_later_rewrite_keeps_the_import_commit(self):
        with self._server("text:0123456789ab") as port:
            STAMP.write_text("abcdef0123456789abcdef\n", encoding="utf-8")
            _raw, body = self._get(port)
        self.assertIn("commit", body)
        self.assertEqual(body["commit"], "0123456789ab")

    def test_d_public_health_has_commit_and_hides_the_lan(self):
        with self._server("text:0123456789ab") as port:
            raw, body = self._get(port, public=True)
        self.assertIn("commit", body)
        self.assertEqual(body["commit"], "0123456789ab")
        self.assertEqual(set(body), PUBLIC_KEYS)
        self.assertNotIn('"host"', raw)
        self.assertNotIn('"port"', raw)
        self.assertNotIn("10.0.0.", raw)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        _serve(sys.argv[2] if len(sys.argv) > 2 else "")
    else:
        unittest.main()
