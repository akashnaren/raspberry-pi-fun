"""Run upload.ingest outside the front server.

The child is a new session so a deadline or a dropped client can kill it
without touching the thread that accepts /health. INLINE is test-only:
unit tests that patch OCR flip it so ingest stays in-process. The sleep
hook PI_PAIR_INGEST_TEST_SLEEP is honored only when PI_PAIR_INGEST_CHILD=1.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

UPLOAD_DEADLINE_S = 45.0
POLL_S = 0.25
INLINE = False

_ROOT = Path(__file__).resolve().parents[2]


def _test_sleep() -> None:
    """Pause only inside the spawned child. The parent never sleeps on this."""
    if os.environ.get("PI_PAIR_INGEST_CHILD") != "1":
        return
    raw = os.environ.get("PI_PAIR_INGEST_TEST_SLEEP", "").strip()
    if not raw:
        return
    try:
        time.sleep(float(raw))
    except ValueError:
        return


def main() -> None:
    """Read one JSON header line, then the raw body, and print one JSON line."""
    _test_sleep()
    from pair.ingest.upload import UploadRejected, ingest

    line = sys.stdin.buffer.readline()
    try:
        header = json.loads(line.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        header = {}
    if not isinstance(header, dict):
        header = {}
    body = sys.stdin.buffer.read()
    try:
        result = ingest(
            str(header.get("content_type") or ""),
            body,
            str(header.get("filename") or ""),
        )
    except UploadRejected as error:
        result = {"ok": False, "error": str(error), "status": error.status}
    except Exception:
        result = {"ok": False, "error": "could not read that file", "status": 422}
    sys.stdout.buffer.write(json.dumps(result).encode() + b"\n")
    sys.stdout.buffer.flush()


def _argv() -> list[str]:
    from pair.flywheel.miss_queue import node_role

    command = ["nice", "-n", "10", sys.executable, "-m", "pair.ingest.job"]
    if node_role() == "brain" and shutil.which("taskset"):
        return ["taskset", "-c", "0", *command]
    return command


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PI_PAIR_INGEST_CHILD"] = "1"
    env.pop("PI_PAIR_OCR_URL", None)
    return env


def _left(deadline: float, gone) -> bool:
    try:
        if gone():
            return True
    except Exception:
        return True
    return time.monotonic() >= deadline


def _stop(proc: subprocess.Popen[bytes], gone) -> dict:
    from pair.ingest.ocr import kill_process_group

    kill_process_group(proc)
    try:
        left = bool(gone())
    except Exception:
        left = True
    if left:
        return {"ok": False, "status": 499, "error": "client left"}
    return {
        "ok": False,
        "status": 504,
        "error": "reading that file took too long",
    }


def _parse_stdout(stdout: bytes) -> dict:
    text = (stdout or b"").decode("utf-8", "replace").strip()
    if not text:
        return {"ok": False, "error": "could not read that file", "status": 422}
    line = text.splitlines()[-1]
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": "could not read that file", "status": 422}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "could not read that file", "status": 422}
    return parsed


def _feed(proc: subprocess.Popen[bytes], payload: bytes) -> None:
    stream = proc.stdin
    if stream is None:
        return
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError):
        try:
            stream.close()
        except OSError:
            pass


def run(
    content_type: str,
    body: bytes,
    filename: str,
    deadline_s: float,
    gone,
) -> dict:
    """Ingest one upload. `gone` is true once the client has left."""
    if INLINE:
        from pair.ingest.upload import UploadRejected, ingest

        try:
            return ingest(content_type, body if body is not None else b"", filename)
        except UploadRejected as error:
            return {"ok": False, "error": str(error), "status": error.status}
    header = (
        json.dumps(
            {"content_type": content_type or "", "filename": filename or ""}
        ).encode()
        + b"\n"
    )
    payload = header + (body or b"")
    try:
        proc = subprocess.Popen(
            _argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(_ROOT),
            env=_child_env(),
        )
    except OSError:
        return {"ok": False, "error": "could not read that file", "status": 422}
    writer = threading.Thread(target=_feed, args=(proc, payload), daemon=True)
    writer.start()
    deadline = time.monotonic() + max(0.0, float(deadline_s))
    stdout = b""
    try:
        while writer.is_alive() and proc.poll() is None:
            if _left(deadline, gone):
                return _stop(proc, gone)
            writer.join(POLL_S)
        # The helper already closed stdin. communicate() would flush it and raise.
        proc.stdin = None
        while True:
            try:
                stdout, _stderr = proc.communicate(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                if _left(deadline, gone):
                    return _stop(proc, gone)
    except (OSError, ValueError):
        return _stop(proc, gone)
    finally:
        writer.join(timeout=0.2)
    return _parse_stdout(stdout or b"")


if __name__ == "__main__":
    main()
