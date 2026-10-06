"""Local OCR for an image or a JPEG-scanned PDF.

Images go to the `tesseract` binary. A scanned PDF is rasterized with
`pdftoppm` from poppler, then each page goes to tesseract. Nothing here
opens a socket or calls a cloud OCR API.

Each of those binaries is limited to OCR_TIMEOUT seconds. On expiry the
whole process group is killed so a child cannot keep running. At most
OCR_SLOTS jobs run at once; the upload path turns the next one away.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

MAX_PDF_PAGES = 5
PDF_DPI = 150
OCR_TIMEOUT = 20
OCR_SLOTS = 2

_GATE = threading.BoundedSemaphore(OCR_SLOTS)


class OcrNotInstalled(Exception):
    """tesseract or pdftoppm is not on PATH."""


class OcrFailed(Exception):
    """The local command ran and did not return text."""


def tesseract_argv() -> list[str]:
    return ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"]


def pdftoppm_argv(pdf: Path, prefix: Path) -> list[str]:
    return [
        "pdftoppm",
        "-jpeg",
        "-r",
        str(PDF_DPI),
        "-f",
        "1",
        "-l",
        str(MAX_PDF_PAGES),
        str(pdf),
        str(prefix),
    ]


def try_acquire() -> bool:
    """Take one OCR slot without waiting. False means the cap is full."""
    return _GATE.acquire(blocking=False)


def release() -> None:
    _GATE.release()


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the session started for this OCR binary, then reap it."""
    if proc.poll() is None and proc.pid:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait(timeout=2)
    _close_pipes(proc)


def _launch_argv(argv: list[str]) -> list[str]:
    """OCR binaries yield the CPU. Other commands stay as the caller wrote them."""
    if argv and argv[0] in {"tesseract", "pdftoppm"}:
        return ["nice", "-n", "10", *argv]
    return list(argv)


def run_local(
    argv: list[str],
    stdin: bytes | None = None,
    timeout: float = OCR_TIMEOUT,
) -> subprocess.CompletedProcess[bytes]:
    """Run one local binary. A timeout kills its process group."""
    launched = _launch_argv(argv)
    try:
        proc = subprocess.Popen(
            launched,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise OcrNotInstalled(argv[0] if argv else "ocr") from exc
    try:
        stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_group(proc)
        raise OcrFailed("timed out") from None
    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        message = detail[-1][:180] if detail else "ocr failed"
        raise OcrFailed(message)
    return subprocess.CompletedProcess(argv, proc.returncode or 0, stdout, stderr)


def recognize_image(data: bytes) -> str:
    """OCR one image. `data` is the file bytes, not a path."""
    if not data:
        raise OcrFailed("empty image")
    proc = run_local(tesseract_argv(), data, timeout=OCR_TIMEOUT)
    text = proc.stdout.decode("utf-8", "replace").replace("\x0c", "\n")
    return text.strip()


def recognize_pdf(data: bytes) -> str:
    """Rasterize the first pages with poppler, then OCR each JPEG."""
    if not data:
        raise OcrFailed("empty pdf")
    with tempfile.TemporaryDirectory(prefix="pi-ocr-") as tmp:
        folder = Path(tmp)
        pdf_path = folder / "in.pdf"
        pdf_path.write_bytes(data)
        prefix = folder / "page"
        run_local(pdftoppm_argv(pdf_path, prefix), None, timeout=OCR_TIMEOUT)
        pages = sorted(folder.glob("page*.jpg"))[:MAX_PDF_PAGES]
        if not pages:
            raise OcrFailed("no pages")
        parts = [recognize_image(page.read_bytes()) for page in pages]
    return "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()
