"""install.sh into a throwaway HOME for each board, then import and boot what it wrote."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOARDS = {"pi2": "health", "pi3": "dataset", "pi4": "brain"}
IMPORT_ALL = (
    "import importlib, pathlib, sys; sys.path.insert(0, '.');"
    "[importlib.import_module('.'.join(p.with_suffix('').parts))"
    " for p in sorted(pathlib.Path('pair').rglob('*.py')) if p.name != '__init__.py'];"
    "import mini_chat"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _unit_env(unit: str) -> dict[str, str]:
    pairs = [
        line[len("Environment=") :]
        for line in unit.splitlines()
        if line.startswith("Environment=")
    ]
    return dict(pair.split("=", 1) for pair in pairs)


class InstallTree(unittest.TestCase):
    def _install(self, home: Path, name: str) -> Path:
        stale = home / "pi-pair" / "pair" / "stale_module.py"
        stale.parent.mkdir(parents=True)
        stale.write_text(
            "raise ImportError('stale module was left on the board')\n",
            encoding="utf-8",
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PI_PAIR_NAME": name,
            "PI_PAIR_DIR": str(home / "pi-pair"),
        }
        done = subprocess.run(
            ["bash", str(ROOT / "install.sh")],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stdout[-2000:] + done.stderr[-2000:])
        self.assertFalse(stale.exists())
        return home / ".config" / "systemd" / "user" / "pi-pair.service"

    def test_each_board_installs_imports_and_serves(self):
        for name, role in BOARDS.items():
            with self.subTest(board=name), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                unit = self._install(home, name).read_text(encoding="utf-8")
                tree = home / "pi-pair"
                self.assertIn("Description=Pi GPT 1.0", unit)
                self.assertIn(f"WorkingDirectory={tree}\n", unit)
                self.assertRegex(unit, rf"ExecStart=\S*python3 {tree}/mini_chat\.py\n")
                env = _unit_env(unit)
                self.assertEqual(env["PI_PAIR_ROLE"], role)

                imported = subprocess.run(
                    [sys.executable, "-c", IMPORT_ALL],
                    cwd=tree,
                    env={"PATH": "/usr/bin:/bin", "PI_PAIR_ROLE": role},
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(imported.returncode, 0, imported.stderr[-2000:])
                self._boot(tree, home, env)

    def _boot(self, tree: Path, home: Path, env: dict[str, str]) -> None:
        peers = json.loads((ROOT / "peers.example.json").read_text(encoding="utf-8"))
        for peer in peers:
            peer["host"], peer["port"] = "127.0.0.1", 9
        (home / "peers-local.json").write_text(json.dumps(peers), encoding="utf-8")
        port = _free_port()
        env = {
            **env,
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "PI_PAIR_HOST": "127.0.0.1",
            "PI_PAIR_PORT": str(port),
            "PI_PAIR_PEERS": str(home / "peers-local.json"),
        }
        proc = subprocess.Popen(
            [sys.executable, "mini_chat.py"],
            cwd=tree,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urllib.request.urlopen(f"{base}/health", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertTrue(json.loads(resp.read())["ok"])
                    break
                except (urllib.error.URLError, ConnectionError):
                    if time.monotonic() > deadline or proc.poll() is not None:
                        raise
                    time.sleep(0.1)
            with urllib.request.urlopen(f"{base}/static/mesh.js", timeout=5) as resp:
                etag = resp.headers["ETag"]
            self.assertTrue(etag)
            again = urllib.request.Request(
                f"{base}/static/mesh.js", headers={"If-None-Match": etag}
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(again, timeout=5)
            self.assertEqual(caught.exception.code, 304)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            if proc.stderr:
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
