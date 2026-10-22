from __future__ import annotations

import subprocess
import unittest

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


if __name__ == "__main__":
    unittest.main()
