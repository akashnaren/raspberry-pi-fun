"""Headless office conversion stays bounded and cleans up after itself."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pair.render import office


def _stopped(pid: int) -> bool:
    """True once the pid is gone or only a zombie."""
    for _ in range(50):
        status = Path(f"/proc/{pid}/status")
        if not status.exists():
            return True
        try:
            text = status.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, ProcessLookupError):
            return True
        state = ""
        for line in text.splitlines():
            if line.startswith("State:"):
                state = line.split()[1]
                break
        if state in {"Z", "X"}:
            return True
        time.sleep(0.02)
    return False


class OfficeConvert(unittest.TestCase):
    def setUp(self):
        fd, self._mark = tempfile.mkstemp(prefix="office-mark-")
        os.close(fd)
        self._old = os.environ.get("PI_PAIR_OFFICE_MARK")
        os.environ["PI_PAIR_OFFICE_MARK"] = self._mark

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PI_PAIR_OFFICE_MARK", None)
        else:
            os.environ["PI_PAIR_OFFICE_MARK"] = self._old
        Path(self._mark).unlink(missing_ok=True)

    def _root(self) -> Path:
        text = Path(self._mark).read_text(encoding="ascii").split()
        self.assertTrue(text)
        return Path(text[-1])

    def test_a_finished_conversion_returns_the_output_and_removes_its_temp_dir(self):
        script = """
import os, sys
from pathlib import Path
args = sys.argv[1:]
outdir = Path(args[args.index("--outdir") + 1])
target = args[args.index("--convert-to") + 1].split(":", 1)[0]
open(os.environ["PI_PAIR_OFFICE_MARK"], "w", encoding="ascii").write(os.getcwd())
(outdir / ("source." + target)).write_text("Hello mesh\\nitem\\n", encoding="utf-8")
"""
        opened = office.convert_office(
            b"Hello mesh",
            "docx",
            "txt",
            timeout=5,
            command=[sys.executable, "-c", script],
        )
        root = self._root()
        self.assertIn(b"Hello mesh", opened)
        self.assertIn(b"item", opened)
        self.assertFalse(root.exists(), root)
        self.assertLessEqual(office.CONVERT_TIMEOUT_S, 60)

    def test_a_hung_conversion_is_killed_and_its_temp_files_are_removed(self):
        script = """
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open("leftover.bin", "wb").write(b"x" * 4096)
child = os.fork()
if child == 0:
    time.sleep(30)
    os._exit(0)
open(os.environ["PI_PAIR_OFFICE_MARK"], "w", encoding="ascii").write(
    "%s %s %s\\n" % (os.getpid(), child, os.getcwd())
)
time.sleep(30)
"""
        started = time.monotonic()
        with self.assertRaises(office.OfficeError) as caught:
            office.convert_office(
                b"Hello mesh",
                "docx",
                "txt",
                timeout=0.4,
                command=[sys.executable, "-c", script],
            )
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(str(caught.exception), "timed out")
        text = Path(self._mark).read_text(encoding="ascii").split()
        self.assertEqual(len(text), 3)
        root = Path(text[2])
        for pid in (int(text[0]), int(text[1])):
            self.assertTrue(_stopped(pid), pid)
        self.assertFalse(root.exists(), root)
        self.assertFalse((root / "leftover.bin").exists())

    def test_an_oversized_output_is_refused_and_its_temp_files_are_removed(self):
        extra = office.MAX_OUTPUT_BYTES + 1
        script = f"""
import os, sys
from pathlib import Path
args = sys.argv[1:]
outdir = Path(args[args.index("--outdir") + 1])
target = args[args.index("--convert-to") + 1].split(":", 1)[0]
open(os.environ["PI_PAIR_OFFICE_MARK"], "w", encoding="ascii").write(os.getcwd())
(outdir / ("source." + target)).write_bytes(b"x" * {extra})
"""
        with self.assertRaises(office.OfficeError) as caught:
            office.convert_office(
                b"Hello mesh",
                "docx",
                "txt",
                timeout=5,
                command=[sys.executable, "-c", script],
            )
        self.assertEqual(str(caught.exception), "output too large")
        self.assertFalse(self._root().exists())

    def test_a_wrapper_that_exits_while_its_child_holds_the_pipes_leaves_no_process(
        self,
    ):
        started = time.monotonic()
        with self.assertRaises(office.OfficeError) as caught:
            office.convert_office(
                b"x",
                "docx",
                "txt",
                timeout=0.4,
                command=[
                    "sh",
                    "-c",
                    'sleep 30 & echo $! > "$PI_PAIR_OFFICE_MARK"; exit 0',
                ],
            )
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(str(caught.exception), "timed out")
        pid = int(Path(self._mark).read_text(encoding="ascii").strip())
        self.assertTrue(_stopped(pid), pid)

    def test_a_wrapper_whose_child_detaches_leaves_no_process(self):
        started = time.monotonic()
        with self.assertRaises(office.OfficeError) as caught:
            office.convert_office(
                b"x",
                "docx",
                "txt",
                timeout=2,
                command=[
                    "sh",
                    "-c",
                    "sleep 30 >/dev/null 2>&1 </dev/null & "
                    'echo $! > "$PI_PAIR_OFFICE_MARK"; exit 0',
                ],
            )
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(str(caught.exception), "no output")
        pid = int(Path(self._mark).read_text(encoding="ascii").strip())
        self.assertTrue(_stopped(pid), pid)

    def test_a_temp_dir_over_the_cap_kills_the_group(self):
        self.assertEqual(office.MAX_TEMP_BYTES, 256 * 1024 * 1024)
        script = """
import os, time
open(os.environ["PI_PAIR_OFFICE_MARK"], "w", encoding="ascii").write(
    "%s %s\\n" % (os.getpid(), os.getcwd())
)
index = 0
while True:
    open("blob%d" % index, "wb").write(b"x" * 20000)
    index += 1
    time.sleep(0.05)
"""
        started = time.monotonic()
        with patch.object(office, "MAX_TEMP_BYTES", 10_000):
            with self.assertRaises(office.OfficeError) as caught:
                office.convert_office(
                    b"x",
                    "docx",
                    "txt",
                    timeout=5,
                    command=[sys.executable, "-c", script],
                )
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(str(caught.exception), "temp dir too large")
        text = Path(self._mark).read_text(encoding="ascii").split()
        self.assertEqual(len(text), 2)
        self.assertTrue(_stopped(int(text[0])), text[0])
        self.assertFalse(Path(text[1]).exists(), text[1])

    def test_a_file_over_the_process_limit_does_not_finish(self):
        script = """
import os
open(os.environ["PI_PAIR_OFFICE_MARK"], "w", encoding="ascii").write(
    "%s\\n" % os.getpid()
)
open("big.bin", "wb").write(b"x" * 100000)
open(os.environ["PI_PAIR_OFFICE_MARK"], "w", encoding="ascii").write("finished\\n")
"""
        with patch.object(office, "_MAX_FILE_BYTES", 8192):
            with self.assertRaises(office.OfficeError):
                office.convert_office(
                    b"x",
                    "docx",
                    "txt",
                    timeout=5,
                    command=[sys.executable, "-c", script],
                )
        text = Path(self._mark).read_text(encoding="ascii")
        self.assertNotIn("finished", text)
        self.assertTrue(_stopped(int(text.split()[0])))
