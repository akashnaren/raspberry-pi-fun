"""deploy_pi3.sh fails closed without production secrets and can print an offline plan."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent / "deploy_pi3.sh"
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
        result = subprocess.run(
            ["bash", str(DEPLOY), "--plan"],
            env=_env(PI3_SSH_KEY=FAKE_KEY),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("data/canned", result.stdout)
        self.assertIn("ci/fixtures/", result.stdout)
        self.assertIn("train_then_delete=yes", result.stdout)
        self.assertIn("no ssh", result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stdout)
        self.assertNotIn("not-a-real-key-material", result.stderr)

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


if __name__ == "__main__":
    unittest.main()
