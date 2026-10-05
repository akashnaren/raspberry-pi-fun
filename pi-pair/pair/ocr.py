"""Local OCR for an image or a JPEG-scanned PDF.

Images go to the `tesseract` binary. A scanned PDF is rasterized with
`pdftoppm` from poppler, then each page goes to tesseract. Nothing here
opens a socket or calls a cloud OCR API.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

MAX_PDF_PAGES = 5
PDF_DPI = 150


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


def run_local(
    argv: list[str],
    stdin: bytes | None = None,
    timeout: float = 45,
) -> subprocess.CompletedProcess[bytes]:
    """Run one local binary. FileNotFoundError becomes OcrNotInstalled."""
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OcrNotInstalled(argv[0] if argv else "ocr") from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrFailed("timed out") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        message = detail[-1][:180] if detail else "ocr failed"
        raise OcrFailed(message)
    return proc


def recognize_image(data: bytes) -> str:
    """OCR one image. `data` is the file bytes, not a path."""
    if not data:
        raise OcrFailed("empty image")
    proc = run_local(tesseract_argv(), data, timeout=45)
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
        run_local(pdftoppm_argv(pdf_path, prefix), None, timeout=60)
        pages = sorted(folder.glob("page*.jpg"))[:MAX_PDF_PAGES]
        if not pages:
            raise OcrFailed("no pages")
        parts = [recognize_image(page.read_bytes()) for page in pages]
    return "\n\n".join(part.strip() for part in parts if part and part.strip()).strip()
