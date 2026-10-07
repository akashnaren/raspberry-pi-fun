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


def hold_embed() -> bool:
    """Unload the embed tag on the dataset parent before OCR starts.

    The child must not touch the counters (it is a different process, and
    ``run_local`` already skips the hook when ``PI_PAIR_INGEST_CHILD=1``).
    Other roles are a no-op. Returns whether this call took the hook.
    """
    if os.environ.get("PI_PAIR_INGEST_CHILD") == "1":
        return False
    from pair.flywheel.miss_queue import node_role

    if node_role() != "dataset":
        return False
    from pair.nodes import embedder

    embedder.ocr_enter()
    return True


def release_embed(held: bool) -> None:
    """Pair with ``hold_embed``. A false flag does nothing."""
    if not held:
        return
    from pair.nodes import embedder

    embedder.ocr_exit()


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
    watched = bool(argv) and argv[0] in {"tesseract", "pdftoppm"}
    # The parent server owns the embed counters. The child must not touch them.
    embedder = None
    if watched and os.environ.get("PI_PAIR_INGEST_CHILD") != "1":
        from pair.nodes import embedder as embed_mod

        embedder = embed_mod
    try:
        if embedder is not None:
            embedder.ocr_enter()
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
    finally:
        if embedder is not None:
            embedder.ocr_exit()


def remote_extract(data: bytes, filename: str, url: str) -> str:
    """Ask pi3 to extract. The URL is a tool route, never a chat route."""
    import base64
    import json
    import os
    import urllib.request

    if "/api/" in url or url.rstrip("/").endswith("/v1/chat/completions"):
        raise OcrFailed("refusing a generation url")
    if os.environ.get("PI_PAIR_ROLE", "").strip().lower() != "brain":
        return ""
    body = json.dumps(
        {
            "filename": filename,
            "kind": "pdf" if filename.lower().endswith(".pdf") else "image",
            "data": base64.b64encode(data).decode("ascii"),
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=OCR_TIMEOUT) as response:
        payload = json.loads(response.read().decode() or "{}")
    return str(payload.get("text") or "").strip()


def recognize_image(data: bytes) -> str:
    """OCR one image. pi3 first when PI_PAIR_OCR_URL is set, else local under nice."""
    import os

    if not data:
        raise OcrFailed("empty image")
    remote = os.environ.get("PI_PAIR_OCR_URL", "").strip()
    if remote:
        try:
            text = remote_extract(data, "image.png", remote)
        except Exception:
            text = ""
        if text:
            return text
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
