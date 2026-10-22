"""deploy_pi3.sh fails closed without production secrets and can print an offline plan."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support.paths import ROOT

DEPLOY = ROOT / "ci" / "deploy_pi3.sh"
STAMP = ROOT / "DEPLOYED_SHA"
KNOWN_SHA = "0123456789abcdef0123456789abcdef01234567"
FAKE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "not-a-real-key-material\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
SECRET_NAMES = (
    "PI3_SSH_HOST",
    "PI3_SSH_USER",
    "PI3_SSH_KEY",
    "PI3_SSH_PORT",
    "PI3_PAIR_DIR",
)
_SSH_KEYSCAN = """#!/bin/sh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTestsOnly'
"""
_SSH_FAKE = """#!/bin/sh
{
  printf 'SSH %s\\n' "$*"
} >> "$TRACE_LOG"
cmd=""
for arg in "$@"; do
  cmd=$arg
done
case "$cmd" in
  *'rm -f --'*DEPLOYED_SHA*)
    rm -f -- "$REMOTE_STAMP"
    ;;
esac
exit 0
"""
_RSYNC_LOG = """#!/bin/sh
{
  printf 'CALL\\n'
  printf '%s\\n' "$*"
  if [ -f "$STAMP_PATH" ]; then
    printf 'STAMP:%s\\n' "$(cat "$STAMP_PATH")"
  else
    printf 'STAMP:ABSENT\\n'
  fi
} >> "$TRACE_LOG"
case "$*" in
  *--delete*) ;;
  *)
    if [ -f "$STAMP_PATH" ]; then
      mkdir -p "$(dirname "$REMOTE_STAMP")"
      cp "$STAMP_PATH" "$REMOTE_STAMP"
    fi
    ;;
esac
exit "$RSYNC_EXIT"
"""
OLD_REMOTE = "1111111111111111111111111111111111111111\n"


def _env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in SECRET_NAMES:
        env.pop(name, None)
    env.update(overrides)
    return env


class DeployPi3(unittest.TestCase):
    def test_missing_secrets_fail_closed(self):
        result = subprocess.run(
            ["bash", str(DEPLOY)],
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PI3_SSH_HOST", result.stderr)
        self.assertIn("PI3_SSH_USER", result.stderr)
        self.assertIn("PI3_SSH_KEY", result.stderr)
        self.assertIn("production", result.stderr)
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", result.stderr)

    def test_dry_run_still_requires_secrets(self):
        result = subprocess.run(
            ["bash", str(DEPLOY), "--dry-run"],
            env=_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required secrets", result.stderr)
        self.assertIn("PI3_SSH_KEY", result.stderr)

    def test_plan_is_offline_and_lists_canned_sync(self):
        env = _env(PI3_SSH_KEY=FAKE_KEY)
        env.pop("GITHUB_SHA", None)
        stamp = ROOT / "DEPLOYED_SHA"
        before = stamp.read_bytes() if stamp.exists() else None
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        result = subprocess.run(
            ["bash", str(DEPLOY), "--plan"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("data/canned", result.stdout)
        self.assertIn("ci/fixtures/", result.stdout)
        self.assertIn("train_then_delete=yes", result.stdout)
        self.assertIn("no ssh", result.stdout)
        self.assertIn("DEPLOYED_SHA", result.stdout)
        self.assertIn(head, result.stdout)
        self.assertIn("data/docs/", result.stdout)
        self.assertIn("data/memory/", result.stdout)
        self.assertIn("tree sync excludes DEPLOYED_SHA", result.stdout)
        self.assertIn("stamp after the tree sync exits 0", result.stdout)
        missing = [
            phrase
            for phrase in ("data/metrics.json", "rm -f", "--checksum")
            if phrase not in result.stdout
        ]
        self.assertEqual(missing, [], result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stderr)
        after = stamp.read_bytes() if stamp.exists() else None
        self.assertEqual(after, before)

    def test_refuses_pi4_host_without_connecting(self):
        result = subprocess.run(
            ["bash", str(DEPLOY), "--dry-run"],
            env=_env(PI3_SSH_HOST="rpi-pi4", PI3_SSH_USER="pi", PI3_SSH_KEY=FAKE_KEY),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing host", result.stderr)
        self.assertNotIn("not-a-real-key-material", result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stderr)

    def test_refuses_delete_outside_pi_pair_dir(self):
        result = subprocess.run(
            ["bash", str(DEPLOY), "--dry-run"],
            env=_env(
                PI3_SSH_HOST="rpi-pi3",
                PI3_SSH_USER="pi",
                PI3_SSH_KEY=FAKE_KEY,
                PI3_PAIR_DIR="/tmp/elsewhere",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be named pi-pair", result.stderr)
        self.assertNotIn("not-a-real-key-material", result.stderr)

    def test_refuses_glob_characters_in_pair_dir(self):
        for dangerous in ("*/pi-pair", "q?/pi-pair", "x[y]/pi-pair"):
            with self.subTest(pair_dir=dangerous):
                with tempfile.TemporaryDirectory() as raw:
                    bindir = Path(raw)
                    flag = bindir / "reached"
                    reached = bindir / "ssh-keyscan"
                    reached.write_text(
                        '#!/bin/sh\ntouch "$REACHED"\nexit 0\n',
                    )
                    reached.chmod(0o755)
                    rsync = bindir / "rsync"
                    rsync.write_text('#!/bin/sh\ntouch "$REACHED"\nexit 0\n')
                    rsync.chmod(0o755)
                    env = _env(
                        PI3_SSH_HOST="rpi-pi3",
                        PI3_SSH_USER="pi",
                        PI3_SSH_KEY=FAKE_KEY,
                        PI3_PAIR_DIR=dangerous,
                        REACHED=str(flag),
                    )
                    env["PATH"] = f"{bindir}:{env['PATH']}"
                    result = subprocess.run(
                        ["bash", str(DEPLOY), "--dry-run"],
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("must not contain", result.stderr)
                self.assertIn(dangerous, result.stderr)
                self.assertFalse(flag.exists(), result.stderr)
                self.assertNotIn("not-a-real-key-material", result.stdout)
                self.assertNotIn("not-a-real-key-material", result.stderr)

    def test_ssh_array_assignment_avoids_declare_g(self):
        text = (ROOT / "ci" / "pi3_ssh.sh").read_text()
        self.assertNotIn("declare -g", text)

    def test_stamp_is_sent_only_after_tree_sync(self):
        before = _stamp_bytes()
        try:
            if STAMP.exists():
                STAMP.unlink()
            with tempfile.TemporaryDirectory() as raw:
                bindir = Path(raw)
                log = bindir / "rsync.log"
                _install_fakes(bindir, "0")
                result = subprocess.run(
                    ["bash", str(DEPLOY)],
                    env=_sync_env(bindir, log),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                log_text = log.read_text() if log.exists() else ""
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = _rsync_calls(log_text)
            self.assertEqual(len(calls), 2, log_text)
            tree, stamp = calls
            self.assertIn("--delete", tree)
            self.assertIn("--exclude DEPLOYED_SHA", tree)
            self.assertIn(f"{ROOT}/", tree)
            self.assertIn("STAMP:ABSENT", tree)
            self.assertNotIn("--delete", stamp)
            self.assertNotIn("--exclude", stamp)
            self.assertIn("--checksum", stamp)
            self.assertIn(f"{ROOT}/DEPLOYED_SHA", stamp)
            self.assertIn(f"STAMP:{KNOWN_SHA}", stamp)
            self.assertIn("rm -f --", log_text)
            self.assertLess(log_text.index("rm -f --"), log_text.index("CALL\n"))
            self.assertEqual(STAMP.read_text(), f"{KNOWN_SHA}\n")
            self.assertNotIn("not-a-real-key-material", result.stdout)
            self.assertNotIn("not-a-real-key-material", result.stderr)
        finally:
            _restore_stamp(before)

    def test_failed_tree_sync_writes_no_stamp(self):
        before = _stamp_bytes()
        try:
            if STAMP.exists():
                STAMP.unlink()
            with tempfile.TemporaryDirectory() as raw:
                bindir = Path(raw)
                log = bindir / "rsync.log"
                _install_fakes(bindir, "1")
                result = subprocess.run(
                    ["bash", str(DEPLOY)],
                    env=_sync_env(bindir, log),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                log_text = log.read_text() if log.exists() else ""
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(STAMP.exists())
            calls = _rsync_calls(log_text)
            self.assertEqual(len(calls), 1, log_text)
            self.assertIn("--delete", calls[0])
            self.assertIn("STAMP:ABSENT", calls[0])
            self.assertNotIn("not-a-real-key-material", result.stdout)
            self.assertNotIn("not-a-real-key-material", result.stderr)
        finally:
            _restore_stamp(before)

    def test_failed_tree_sync_leaves_no_remote_stamp(self):
        before = _stamp_bytes()
        try:
            if STAMP.exists():
                STAMP.unlink()
            with tempfile.TemporaryDirectory() as raw:
                bindir = Path(raw)
                log = bindir / "trace.log"
                remote = bindir / "remote" / "pi-pair" / "DEPLOYED_SHA"
                remote.parent.mkdir(parents=True)
                remote.write_text(OLD_REMOTE)
                _install_fakes(bindir, "20")
                result = subprocess.run(
                    ["bash", str(DEPLOY)],
                    env=_sync_env(bindir, log, remote),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                log_text = log.read_text() if log.exists() else ""
                remote_left = remote.exists()
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse(STAMP.exists())
            self.assertFalse(remote_left, log_text)
            self.assertIn("rm -f --", log_text)
            self.assertIn("pi-pair/DEPLOYED_SHA", log_text)
            self.assertLess(log_text.index("rm -f --"), log_text.index("CALL\n"))
            self.assertNotIn("not-a-real-key-material", result.stdout)
            self.assertNotIn("not-a-real-key-material", result.stderr)
        finally:
            _restore_stamp(before)

    def test_dry_run_does_not_write_local_stamp(self):
        before = _stamp_bytes()
        try:
            if STAMP.exists():
                STAMP.unlink()
            with tempfile.TemporaryDirectory() as raw:
                bindir = Path(raw)
                log = bindir / "trace.log"
                remote = bindir / "remote" / "pi-pair" / "DEPLOYED_SHA"
                remote.parent.mkdir(parents=True)
                remote.write_text(OLD_REMOTE)
                _install_fakes(bindir, "0")
                result = subprocess.run(
                    ["bash", str(DEPLOY), "--dry-run"],
                    env=_sync_env(bindir, log, remote),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                log_text = log.read_text() if log.exists() else ""
                remote_text = remote.read_text() if remote.exists() else None
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(STAMP.exists())
            self.assertEqual(remote_text, OLD_REMOTE)
            self.assertNotIn("rm -f --", log_text)
            calls = _rsync_calls(log_text)
            self.assertEqual(len(calls), 1, log_text)
            self.assertIn("-n", calls[0])
            self.assertNotIn("not-a-real-key-material", result.stdout)
            self.assertNotIn("not-a-real-key-material", result.stderr)
        finally:
            _restore_stamp(before)


def _stamp_bytes() -> bytes | None:
    if STAMP.exists():
        return STAMP.read_bytes()
    return None


def _restore_stamp(before: bytes | None) -> None:
    for leftover in ROOT.glob("DEPLOYED_SHA.tmp.*"):
        leftover.unlink()
    if before is None:
        STAMP.unlink(missing_ok=True)
        return
    STAMP.write_bytes(before)


def _install_fakes(bindir: Path, rsync_exit: str) -> None:
    for name, body in (
        ("ssh-keyscan", _SSH_KEYSCAN),
        ("ssh", _SSH_FAKE),
        ("rsync", _RSYNC_LOG),
    ):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    (bindir / "rsync.exit").write_text(rsync_exit)


def _sync_env(bindir: Path, log: Path, remote: Path | None = None) -> dict[str, str]:
    env = _env(
        PI3_SSH_HOST="rpi-pi3",
        PI3_SSH_USER="pi",
        PI3_SSH_KEY=FAKE_KEY,
        GITHUB_SHA=KNOWN_SHA,
        TRACE_LOG=str(log),
        RSYNC_EXIT=(bindir / "rsync.exit").read_text(),
        STAMP_PATH=str(STAMP),
        REMOTE_STAMP=str(remote or (bindir / "remote-stamp")),
    )
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def _rsync_calls(log: str) -> list[str]:
    return [block for block in log.split("CALL\n") if "STAMP:" in block]


if __name__ == "__main__":
    unittest.main()
