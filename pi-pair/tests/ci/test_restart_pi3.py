from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.ci.test_deploy_pi3 import FAKE_KEY, _env
from tests.support.paths import ROOT

RESTART = ROOT / "ci" / "restart_pi3.sh"
EXAMPLE = (
    "GITHUB_SHA=0123456789abcdef0123456789abcdef01234567 bash pi-pair/ci/restart_pi3.sh"
)


def _run(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RESTART), *args],
        env=_env() if env is None else env,
        capture_output=True,
        text=True,
        check=False,
    )


class RestartPi3(unittest.TestCase):
    def test_help_and_dry_run_need_no_secrets(self):
        shown = _run("--help")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("bash pi-pair/ci/restart_pi3.sh --dry-run", shown.stdout)
        self.assertIn(EXAMPLE, shown.stdout)
        self.assertNotIn("ssh-keyscan", shown.stdout)
        self.assertNotIn("ssh-keyscan", shown.stderr)

        env = _env()
        env.pop("GITHUB_SHA", None)
        dry = _run("--dry-run", env=env)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("restart_pi3: dry-run; not restarting", dry.stdout)
        self.assertNotIn("ssh-keyscan", dry.stdout)
        self.assertNotIn("ssh-keyscan", dry.stderr)

        stamped = _env(GITHUB_SHA="0123456789abcdef0123456789abcdef01234567")
        accepted = _run("--dry-run", env=stamped)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("expect_commit: 0123456789ab", accepted.stdout)
        self.assertNotIn("ssh-keyscan", accepted.stdout)
        self.assertNotIn("ssh-keyscan", accepted.stderr)

    def test_refuses_pi4_host_without_connecting(self):
        result = _run(
            env=_env(
                PI3_SSH_HOST="rpi-pi4",
                PI3_SSH_USER="pi",
                PI3_SSH_KEY=FAKE_KEY,
                GITHUB_SHA="0123456789abcdef0123456789abcdef01234567",
            )
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing host", result.stderr)
        self.assertNotIn("ssh-keyscan", result.stdout)
        self.assertNotIn("ssh-keyscan", result.stderr)
        self.assertNotIn("not-a-real-key-material", result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stderr)

    def test_idle_wait_then_commit_poll(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        expect = "0123456789ab"
        with tempfile.TemporaryDirectory() as raw:
            bindir = Path(raw)
            log = bindir / "ssh.log"
            queue = bindir / "health.queue"
            queue.write_text(
                "\n".join(
                    [
                        '{"ok":true,"commit":"old","in_flight":1,"waiting":0}',
                        '{"ok":true,"commit":"old","in_flight":0,"waiting":0}',
                        '{"ok":true,"commit":"000000000000","in_flight":0,"waiting":0}',
                        '{"ok":true,"commit":"0123456789ab","in_flight":0,"waiting":0}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            _install_restart_fakes(bindir)
            env = _env(
                PI3_SSH_HOST="rpi-pi3",
                PI3_SSH_USER="pi",
                PI3_SSH_KEY=FAKE_KEY,
                GITHUB_SHA=sha,
                SSH_LOG=str(log),
                HEALTH_QUEUE=str(queue),
            )
            env["PATH"] = f"{bindir}:{env['PATH']}"
            result = _run(env=env)
            text = log.read_text(encoding="utf-8") if log.exists() else ""
        self.assertEqual(result.returncode, 0, result.stderr + text)
        self.assertIn(f"restart_pi3: ok commit={expect}", result.stdout)
        lines = text.splitlines()
        health_at = [i for i, line in enumerate(lines) if "18080/health" in line]
        sleep_at = [i for i, line in enumerate(lines) if line.startswith("SLEEP")]
        restart_at = [i for i, line in enumerate(lines) if "systemctl" in line]
        self.assertEqual(len(health_at), 4, text)
        self.assertEqual(len(restart_at), 1, text)
        self.assertGreaterEqual(len(sleep_at), 2, text)
        self.assertLess(health_at[0], sleep_at[0])
        self.assertLess(sleep_at[0], health_at[1])
        self.assertLess(health_at[1], restart_at[0])
        self.assertLess(restart_at[0], health_at[2])
        self.assertLess(health_at[2], sleep_at[1])
        self.assertLess(sleep_at[1], health_at[3])
        self.assertNotIn("not-a-real-key-material", result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stderr)

    def test_idle_drop_is_documented(self):
        ops = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        script = RESTART.read_text(encoding="utf-8")
        phrase = "in-flight chats are dropped"
        self.assertIn(phrase, ops)
        self.assertIn(phrase, script)


_SSH_KEYSCAN = """#!/bin/sh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTestsOnly'
"""
_SSH = """#!/bin/sh
cmd=""
for arg in "$@"; do
  cmd=$arg
done
printf '%s\\n' "$cmd" >> "$SSH_LOG"
case "$cmd" in
  *systemctl*restart*)
    exit 0
    ;;
  *18080/health*)
    line=$(head -n 1 "$HEALTH_QUEUE")
    tail -n +2 "$HEALTH_QUEUE" > "$HEALTH_QUEUE.next"
    mv "$HEALTH_QUEUE.next" "$HEALTH_QUEUE"
    printf '%s\\n' "$line"
    exit 0
    ;;
esac
exit 0
"""
_SLEEP = """#!/bin/sh
printf 'SLEEP %s\\n' "$*" >> "$SSH_LOG"
exit 0
"""


def _install_restart_fakes(bindir: Path) -> None:
    for name, body in (
        ("ssh-keyscan", _SSH_KEYSCAN),
        ("ssh", _SSH),
        ("sleep", _SLEEP),
    ):
        path = bindir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
