"""MIME routing for attachment ingest. OCR is mocked; no tesseract in CI."""

from __future__ import annotations

import base64
import binascii
import json
import os
import socket
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from unittest.mock import patch

from pair.core import runtime
from pair.ingest import job as ingest_job
from pair.ingest import ocr, upload
from pair.ingest.pdftext import extract_pdf_text
from pair.server import make_server
from tests.support.paths import ROOT
from tests.support.web import web_source

JPEG = b"\xff\xd8\xff\xd9"
JPEG_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /XObject /Subtype /Image /Filter /DCTDecode /Length 4 >> stream\n"
    + JPEG
    + b"\nendstream endobj\n%%EOF\n"
)
TEXT_PDF = b"%PDF-1.4\n1 0 obj << /Length 5 >> stream\n(Hi)\nendstream endobj\n%%EOF\n"


def _flate_pdf(text: str) -> bytes:
    """A small valid PDF whose page text is a FlateDecode Tj operator."""
    content = f"BT /F1 12 Tf ({text}) Tj ET".encode("ascii")
    raw = zlib.compress(content)
    header = b"%PDF-1.4\n"
    chunks = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>\nendobj\n",
        (
            b"4 0 obj\n<< /Length "
            + str(len(raw)).encode("ascii")
            + b" /Filter /FlateDecode >>\nstream\n"
            + raw
            + b"\nendstream\nendobj\n"
        ),
    ]
    offsets = [0]
    body = b""
    cursor = len(header)
    for chunk in chunks:
        offsets.append(cursor)
        body += chunk
        cursor += len(chunk)
    xref = "xref\n0 5\n0000000000 65535 f \n" + "".join(
        f"{off:010d} 00000 n \n" for off in offsets[1:]
    )
    trailer = (
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return header + body + xref.encode("ascii") + trailer


def _content_pdf(stream: bytes, filter_clause: bytes, decoy: bytes = b"") -> bytes:
    """One-page PDF whose content stream uses filter_clause."""
    header = b"%PDF-1.4\n"
    page = (
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200]"
        + decoy
        + b" /Contents 4 0 R >>\nendobj\n"
    )
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        page,
        (
            b"4 0 obj\n<< /Length "
            + str(len(stream)).encode("ascii")
            + b" "
            + filter_clause
            + b" >>\nstream\n"
            + stream
            + b"\nendstream\nendobj\n"
        ),
    ]
    body = b""
    cursor = len(header)
    offsets = []
    for chunk in objects:
        offsets.append(cursor)
        body += chunk
        cursor += len(chunk)
    xref = "xref\n0 5\n0000000000 65535 f \n" + "".join(
        f"{off:010d} 00000 n \n" for off in offsets
    )
    trailer = (
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return header + body + xref.encode("ascii") + trailer


def _multipart(name: str, mime: str, data: bytes) -> tuple[str, bytes]:
    boundary = "----pi-pair-test"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", body


class MimeRouting(unittest.TestCase):
    def test_text_and_ocr_routes(self):
        cases = [
            ("notes.txt", "text/plain", b"hello", "text"),
            ("notes.md", "text/markdown", b"# hello", "text"),
            ("notes.markdown", "application/octet-stream", b"hello", "text"),
            ("NOTES.TXT", "application/octet-stream", b"hello", "text"),
            ("readme.md", "text/plain", b"hello", "text"),
            ("pic.jpg", "image/jpeg", JPEG, "ocr"),
            ("pic.jpeg", "application/octet-stream", JPEG, "ocr"),
            ("pic.png", "image/png", b"\x89PNG\r\n\x1a\n", "ocr"),
            ("pic.gif", "image/gif", b"GIF89a", "ocr"),
            ("pic.webp", "image/webp", b"RIFF\x00\x00\x00\x00WEBP", "ocr"),
            ("pic.tif", "image/tiff", b"II*\x00", "ocr"),
            ("pic.bmp", "image/bmp", b"BM" + b"\x00" * 30, "ocr"),
            ("scan.pdf", "application/pdf", JPEG_PDF, "ocr"),
            ("scan.PDF", "application/octet-stream", JPEG_PDF, "ocr"),
            ("shot", "image/png", b"\x89PNG\r\n\x1a\n", "ocr"),
        ]
        for name, mime, data, route in cases:
            with self.subTest(name=name, mime=mime):
                self.assertEqual(upload.route_for(name, mime, data), route)

    def test_text_pdf_and_unknown_types_are_rejected(self):
        with self.assertRaises(upload.UploadRejected) as pdf_error:
            upload.route_for("essay.pdf", "application/pdf", TEXT_PDF)
        self.assertEqual(pdf_error.exception.status, 415)
        self.assertEqual(str(pdf_error.exception), "that PDF has no readable text")
        self.assertFalse(upload.is_jpeg_scanned_pdf(TEXT_PDF))
        self.assertTrue(upload.is_jpeg_scanned_pdf(JPEG_PDF))
        essay = _flate_pdf("Hello")
        self.assertIn(b"xref", essay)
        self.assertEqual(upload.route_for("essay.pdf", "application/pdf", essay), "pdf")
        self.assertEqual(
            upload.route_for("essay.pdf", "application/pdf", essay + b"\xff\xd8\xff"),
            "pdf",
        )
        self.assertFalse(upload.is_jpeg_scanned_pdf(essay + b"\xff\xd8\xff"))
        with self.assertRaises(upload.UploadRejected) as csv_error:
            upload.route_for("rows.csv", "text/csv", b"a,b")
        self.assertEqual(csv_error.exception.status, 415)
        self.assertEqual(str(csv_error.exception), "unsupported file type")

    def test_reportlab_and_hex_filters_keep_page_text(self):
        page = b"BT /F1 12 Tf 72 720 Td (Secret code word: PINEAPPLE) Tj ET"
        encoded = base64.a85encode(zlib.compress(page)) + b"~>"
        report = _content_pdf(
            encoded,
            b"/Filter [ /ASCII85Decode /FlateDecode ]",
            decoy=b" /Filter /LZWDecode",
        )
        self.assertEqual(upload.route_for("note.pdf", "application/pdf", report), "pdf")
        ingested = upload.ingest("application/pdf", report, filename="note.pdf")
        self.assertIn("PINEAPPLE", ingested["text"])

        folded = b"<~" + b"\n".join(
            encoded[index : index + 16] for index in range(0, len(encoded) - 2, 16)
        )
        if not folded.endswith(b"~>"):
            folded += b"~>"
        wrapped = _content_pdf(
            folded,
            b"/Filter [ /ASCII85Decode /FlateDecode ]",
        )
        self.assertIn("PINEAPPLE", extract_pdf_text(wrapped))

        hex_page = binascii.hexlify(b"BT (Hex word) Tj ET") + b"0>"
        hex_pdf = _content_pdf(hex_page, b"/Filter /ASCIIHexDecode")
        self.assertEqual(upload.route_for("hex.pdf", "application/pdf", hex_pdf), "pdf")
        self.assertIn(
            "Hex word",
            upload.ingest("application/pdf", hex_pdf, filename="hex.pdf")["text"],
        )

        skipped = _content_pdf(
            b"BT (Hidden) Tj ET",
            b"/Filter /LZWDecode",
        )
        self.assertEqual(extract_pdf_text(skipped), "")
        kept = _content_pdf(
            zlib.compress(b"BT (One) Tj <54776F> Tj ET\nBT (Two) Tj ET") + b"junk",
            b"/Filter /FlateDecode",
        )
        self.assertEqual(extract_pdf_text(kept), "One Two\nTwo")

    def test_txt_extension_stays_text_and_jpeg_named_pdf_is_an_image(self):
        self.assertEqual(
            upload.route_for("notes.txt", "application/octet-stream", JPEG), "text"
        )
        self.assertEqual(upload.route_for("scan.pdf", "application/pdf", JPEG), "ocr")
        self.assertEqual(upload.ocr_kind("scan.pdf", "application/pdf", JPEG), "image")
        self.assertEqual(
            upload.ocr_kind("scan.pdf", "application/pdf", JPEG_PDF), "pdf"
        )

    def test_text_ingest_does_not_call_ocr(self):
        def boom(_data):
            raise AssertionError("ocr should not run for text")

        previous_image = ocr.recognize_image
        previous_pdf = ocr.recognize_pdf
        ocr.recognize_image = boom
        ocr.recognize_pdf = boom
        try:
            result = upload.ingest(
                "text/markdown; charset=utf-8",
                b"\xef\xbb\xbf# title\n\nbody",
                filename="notes.md",
            )
        finally:
            ocr.recognize_image = previous_image
            ocr.recognize_pdf = previous_pdf
        self.assertEqual(result["route"], "text")
        self.assertEqual(result["text"], "# title\n\nbody")
        essay = upload.ingest(
            "application/pdf", _flate_pdf("Hello"), filename="note.pdf"
        )
        self.assertEqual(essay["route"], "pdf")
        self.assertEqual(essay["text"], "Hello")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["chars"], len(result["text"]))

    def test_image_and_scanned_pdf_share_the_text_result(self):
        previous_image = ocr.recognize_image
        previous_pdf = ocr.recognize_pdf
        ocr.recognize_image = lambda _data: "from image"
        ocr.recognize_pdf = lambda _data: "from pdf"
        try:
            image = upload.ingest("image/jpeg", JPEG, filename="pic.jpg")
            scanned = upload.ingest("application/pdf", JPEG_PDF, filename="scan.pdf")
        finally:
            ocr.recognize_image = previous_image
            ocr.recognize_pdf = previous_pdf
        self.assertEqual(image["route"], "ocr")
        self.assertEqual(image["text"], "from image")
        self.assertEqual(scanned["route"], "ocr")
        self.assertEqual(scanned["text"], "from pdf")

    def test_text_is_capped(self):
        previous = upload.MAX_TEXT_CHARS
        upload.MAX_TEXT_CHARS = 5
        try:
            result = upload.ingest("text/plain", b"abcdefghi", filename="notes.txt")
        finally:
            upload.MAX_TEXT_CHARS = previous
        self.assertTrue(result["truncated"])
        self.assertEqual(result["text"], "abcde")
        self.assertEqual(result["chars"], 5)

    def test_empty_ocr_and_missing_binary(self):
        previous = ocr.recognize_image
        ocr.recognize_image = lambda _data: "  "
        try:
            with self.assertRaises(upload.UploadRejected) as empty:
                upload.ingest("image/png", b"\x89PNG\r\n\x1a\n", filename="a.png")
        finally:
            ocr.recognize_image = previous
        self.assertEqual(empty.exception.status, 422)

        def missing_binary(_data):
            raise ocr.OcrNotInstalled("tesseract")

        def failed(_data):
            raise ocr.OcrFailed("nope")

        ocr.recognize_image = missing_binary
        try:
            with self.assertRaises(upload.UploadRejected) as missing:
                upload.ingest("image/png", b"\x89PNG\r\n\x1a\n", filename="a.png")
        finally:
            ocr.recognize_image = previous
        self.assertEqual(missing.exception.status, 503)
        self.assertEqual(str(missing.exception), "OCR is not installed on this Pi")
        ocr.recognize_image = failed
        try:
            with self.assertRaises(upload.UploadRejected) as broken:
                upload.ingest("image/png", b"\x89PNG\r\n\x1a\n", filename="a.png")
        finally:
            ocr.recognize_image = previous
        self.assertEqual(broken.exception.status, 422)
        self.assertEqual(str(broken.exception), "could not read that file")

    def test_size_cap_stops_before_a_huge_body(self):
        seen = {"n": 0}

        def read(count):
            seen["n"] += count
            return b"x" * count

        previous = upload.MAX_UPLOAD_BYTES
        upload.MAX_UPLOAD_BYTES = 32
        try:
            with self.assertRaises(upload.UploadRejected) as huge:
                upload.read_limited(str(9 * 1024 * 1024), read)
            self.assertEqual(huge.exception.status, 413)
            self.assertLess(seen["n"], 1024)
            seen["n"] = 0
            with self.assertRaises(upload.UploadRejected) as small:
                upload.read_limited("40", read)
            self.assertEqual(small.exception.status, 413)
            self.assertEqual(seen["n"], 40)
            with self.assertRaises(upload.UploadRejected) as bad:
                upload.read_limited("nope", read)
            self.assertEqual(bad.exception.status, 400)
        finally:
            upload.MAX_UPLOAD_BYTES = previous
        self.assertEqual(upload.MAX_UPLOAD_BYTES, 4 * 1024 * 1024)
        self.assertEqual(upload.MAX_TEXT_CHARS, 4096)

    def test_ocr_commands_are_local_binaries(self):
        image = ocr.tesseract_argv()
        pdf = ocr.pdftoppm_argv(Path("in.pdf"), Path("page"))
        self.assertEqual(image[0], "tesseract")
        self.assertEqual(pdf[0], "pdftoppm")
        self.assertIn("-l", pdf)
        self.assertIn(str(ocr.MAX_PDF_PAGES), pdf)
        self.assertEqual(ocr.MAX_PDF_PAGES, 5)
        self.assertEqual(ocr.OCR_TIMEOUT, 20)
        self.assertGreaterEqual(ocr.OCR_SLOTS, 1)
        self.assertLessEqual(ocr.OCR_SLOTS, 2)
        blob = " ".join(image + pdf)
        self.assertNotIn("http", blob)
        self.assertNotIn("://", blob)
        with self.assertRaises(ocr.OcrNotInstalled):
            ocr.run_local(["__pi_pair_no_such_ocr_bin__"], b"")
        with self.assertRaises(ocr.OcrFailed):
            ocr.run_local([sys.executable, "-c", "import sys; sys.exit(2)"])

    def test_ocr_binaries_take_the_embed_unload_hook(self):
        calls = []

        def enter() -> None:
            calls.append("enter")

        def leave() -> None:
            calls.append("exit")

        with (
            patch("pair.nodes.embedder.ocr_enter", enter),
            patch("pair.nodes.embedder.ocr_exit", leave),
            patch(
                "pair.ingest.ocr._launch_argv",
                return_value=[sys.executable, "-c", "import time; time.sleep(30)"],
            ),
        ):
            started = time.monotonic()
            with self.assertRaises(ocr.OcrFailed):
                ocr.run_local(["tesseract"], timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(calls, ["enter", "exit"])
        calls.clear()
        with (
            patch("pair.nodes.embedder.ocr_enter", enter),
            patch("pair.nodes.embedder.ocr_exit", leave),
        ):
            done = ocr.run_local(["echo", "hi"])
        self.assertEqual(done.returncode, 0)
        self.assertEqual(calls, [])

    def test_timeout_kills_the_process_group(self):
        script = (
            "import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "path, child = sys.argv[1], os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(30)\n"
            "    os._exit(0)\n"
            "open(path, 'w', encoding='ascii').write('%s %s\\n' % (os.getpid(), child))\n"
            "time.sleep(30)\n"
        )
        fd, name = tempfile.mkstemp(prefix="ocr-pids-")
        os.close(fd)
        started = time.monotonic()
        try:
            with self.assertRaises(ocr.OcrFailed) as caught:
                ocr.run_local([sys.executable, "-c", script, name], timeout=0.4)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(str(caught.exception), "timed out")
            text = Path(name).read_text(encoding="ascii").split()
            self.assertEqual(len(text), 2)
            for pid in (int(text[0]), int(text[1])):
                self.assertTrue(_stopped(pid), pid)
        finally:
            Path(name).unlink(missing_ok=True)

    def test_full_ocr_cap_returns_429_without_waiting(self):
        def boom(_data):
            raise AssertionError("ocr ran while the cap was full")

        previous = ocr.recognize_image
        ocr.recognize_image = boom
        held = 0
        try:
            for _ in range(ocr.OCR_SLOTS):
                self.assertTrue(ocr.try_acquire())
                held += 1
            started = time.monotonic()
            with self.assertRaises(upload.UploadRejected) as busy:
                upload.ingest("image/png", b"\x89PNG\r\n\x1a\n", filename="a.png")
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(busy.exception.status, 429)
            self.assertEqual(str(busy.exception), "OCR is busy")
            text = upload.ingest("text/plain", b"hello", filename="a.txt")
            self.assertEqual(text["route"], "text")
        finally:
            ocr.recognize_image = previous
            for _ in range(held):
                ocr.release()


def _s0_pdf(image_bytes: int = 3_200_000) -> bytes:
    """One text stream plus one random Flate image. This is the wedge fixture."""
    image = zlib.compress(os.urandom(image_bytes))
    text = zlib.compress(b"BT (Hello scan) Tj ET")
    return (
        b"%PDF-1.4\n"
        + b"1 0 obj << /Length %d /Filter /FlateDecode >>\nstream\n" % len(text)
        + text
        + b"\nendstream\nendobj\n"
        + b"2 0 obj << /Type /XObject /Subtype /Image /Filter /FlateDecode /Length %d >>\nstream\n"
        % len(image)
        + image
        + b"\nendstream\nendobj\n%%EOF"
    )


def _flate_image_pdf() -> bytes:
    """A PNG-shaped scan: Flate image, no text operators."""
    image = zlib.compress(b"\x00" * 64)
    return (
        b"%PDF-1.4\n1 0 obj << /Type /XObject /Subtype /Image "
        b"/Filter /FlateDecode /Length "
        + str(len(image)).encode("ascii")
        + b" >>\nstream\n"
        + image
        + b"\nendstream\nendobj\n%%EOF"
    )


class PdfTextBounds(unittest.TestCase):
    def test_s0_fixture_returns_the_page_text_quickly(self):
        pdf = _s0_pdf()
        started = time.monotonic()
        text = extract_pdf_text(pdf)
        elapsed = time.monotonic() - started
        self.assertEqual(text, "Hello scan")
        self.assertLess(elapsed, 1.0)

    def test_zip_bomb_stream_stays_small_and_fast(self):
        zeros = b"\x00" * 20_000_000
        packed = zlib.compress(zeros)
        del zeros
        pdf = (
            b"%PDF-1.4\n1 0 obj << /Length "
            + str(len(packed)).encode("ascii")
            + b" /Filter /FlateDecode >>\nstream\n"
            + packed
            + b"\nendstream\nendobj\n%%EOF"
        )
        tracemalloc.start()
        started = time.monotonic()
        extract_pdf_text(pdf)
        elapsed = time.monotonic() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(elapsed, 0.5)
        self.assertLess(peak, 30 * 1024 * 1024)

    def test_brackets_without_tj_stay_linear(self):
        stream = b"[" * 50_000
        pdf = _content_pdf(stream, b"")
        started = time.monotonic()
        self.assertEqual(extract_pdf_text(pdf), "")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_extract_does_not_stall_another_thread(self):
        pdf = _s0_pdf()
        gaps: list[float] = []
        stop = threading.Event()

        def tick() -> None:
            last = time.monotonic()
            while not stop.is_set():
                time.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        thread = threading.Thread(target=tick, daemon=True)
        thread.start()
        try:
            self.assertEqual(extract_pdf_text(pdf), "Hello scan")
        finally:
            stop.set()
            thread.join(timeout=1)
        self.assertTrue(gaps)
        self.assertLess(max(gaps), 0.1)

    def test_ingest_parses_a_text_pdf_once(self):
        calls = {"n": 0}
        real = upload.extract_pdf_text

        def wrapped(data: bytes) -> str:
            calls["n"] += 1
            return real(data)

        with patch("pair.ingest.upload.extract_pdf_text", wrapped):
            result = upload.ingest(
                "application/pdf", _flate_pdf("Hello"), filename="note.pdf"
            )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(result["text"], "Hello")
        self.assertEqual(result["route"], "pdf")

    def test_flate_image_pdf_routes_to_ocr(self):
        scanned = _flate_image_pdf()
        self.assertTrue(upload.is_jpeg_scanned_pdf(scanned))
        self.assertEqual(
            upload.route_for("scan.pdf", "application/pdf", scanned), "ocr"
        )
        self.assertEqual(
            upload.route_hint("notes.txt", "application/octet-stream"), "text"
        )
        self.assertEqual(
            upload.route_hint("scan.pdf", "application/pdf", scanned[:32]), ""
        )


class AttachmentHttp(unittest.TestCase):
    def setUp(self):
        self._inline = ingest_job.INLINE
        ingest_job.INLINE = True
        self.httpd = make_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.port = self.httpd.server_address[1]
        self._image = ocr.recognize_image
        self._pdf = ocr.recognize_pdf
        self.calls = {"image": 0, "pdf": 0}

        def image(_data):
            self.calls["image"] += 1
            return "from image"

        def pdf(_data):
            self.calls["pdf"] += 1
            return "from pdf"

        ocr.recognize_image = image
        ocr.recognize_pdf = pdf

    def tearDown(self):
        ocr.recognize_image = self._image
        ocr.recognize_pdf = self._pdf
        ingest_job.INLINE = self._inline
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, data: bytes, headers: dict[str, str]):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/attachments",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            return error.code, json.loads(raw or "{}")

    def test_multipart_txt_md_image_and_scanned_pdf(self):
        status, body = self._post(
            *_as_post(_multipart("notes.txt", "text/plain", b"alpha"))
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "text")
        self.assertEqual(body["text"], "alpha")
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

        status, body = self._post(
            *_as_post(_multipart("../notes.md", "text/markdown", b"# beta"))
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "notes.md")
        self.assertEqual(body["text"], "# beta")
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

        status, body = self._post(*_as_post(_multipart("pic.jpg", "image/jpeg", JPEG)))
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "ocr")
        self.assertEqual(body["text"], "from image")
        self.assertEqual(self.calls, {"image": 1, "pdf": 0})

        status, body = self._post(
            *_as_post(_multipart("scan.pdf", "application/pdf", JPEG_PDF))
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "ocr")
        self.assertEqual(body["text"], "from pdf")
        self.assertEqual(self.calls, {"image": 1, "pdf": 1})

        status, body = self._post(
            *_as_post(_multipart("note.pdf", "application/pdf", _flate_pdf("Hello")))
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["route"], "pdf")
        self.assertEqual(body["text"], "Hello")
        self.assertEqual(self.calls, {"image": 1, "pdf": 1})

    def test_raw_body_uses_filename_header(self):
        status, body = self._post(
            b"plain line",
            {"content-type": "text/plain", "x-filename": "note.txt"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "text")
        self.assertEqual(body["text"], "plain line")
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

    def test_text_pdf_csv_and_oversize(self):
        status, body = self._post(
            *_as_post(_multipart("essay.pdf", "application/pdf", TEXT_PDF))
        )
        self.assertEqual(status, 415)
        self.assertEqual(
            body["error"], "That PDF could not be read. Try a text file or a photo."
        )
        self.assertNotIn("JPEG-scanned", body["error"])
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

        status, body = self._post(*_as_post(_multipart("rows.csv", "text/csv", b"a,b")))
        self.assertEqual(status, 415)
        self.assertEqual(body["error"], "That file type is not supported.")

        previous = upload.MAX_UPLOAD_BYTES
        upload.MAX_UPLOAD_BYTES = 32
        try:
            status, body = self._post(
                b"y" * 40, {"content-type": "text/plain", "x-filename": "notes.txt"}
            )
        finally:
            upload.MAX_UPLOAD_BYTES = previous
        self.assertEqual(status, 413)
        self.assertEqual(
            body["error"], "That is too big to send. Try a shorter message."
        )

    def test_extract_accepts_a_three_megabyte_image(self):
        blob = b"\xff\xd8\xff" + b"\x00" * (3 * 1024 * 1024)
        raw = json.dumps(
            {
                "filename": "pic.jpg",
                "content_type": "image/jpeg",
                "data": base64.b64encode(blob).decode("ascii"),
            }
        ).encode()
        self.assertGreater(len(raw), 1_000_000)
        self.assertLess(len(raw), 6 * 1024 * 1024)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/tools/extract",
            data=raw,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode())
            status = response.status
        self.assertEqual(status, 200, body)
        self.assertNotEqual(status, 413)
        self.assertEqual(body["text"], "from image")

    def test_extra_ocr_is_rejected_while_slots_are_held(self):
        hold = threading.Event()
        arrived = threading.Semaphore(0)

        def image(_data):
            arrived.release()
            if not hold.wait(5):
                raise ocr.OcrFailed("held too long")
            return "from image"

        ocr.recognize_image = image
        threads = []
        try:
            for _ in range(ocr.OCR_SLOTS):
                thread = threading.Thread(
                    target=lambda: self._post(
                        *_as_post(_multipart("pic.jpg", "image/jpeg", JPEG))
                    )
                )
                thread.start()
                threads.append(thread)
            for _ in range(ocr.OCR_SLOTS):
                self.assertTrue(arrived.acquire(timeout=2))
            started = time.monotonic()
            status, body = self._post(
                *_as_post(_multipart("again.jpg", "image/jpeg", JPEG))
            )
            self.assertLess(time.monotonic() - started, 1)
            self.assertIn(status, (429, 503))
            self.assertEqual(
                body["error"], "Reading a file is busy. Try again in a moment."
            )
            text_status, text_body = self._post(
                b"still text",
                {"content-type": "text/plain", "x-filename": "note.txt"},
            )
            self.assertEqual(text_status, 200)
            self.assertEqual(text_body["route"], "text")
        finally:
            hold.set()
            for thread in threads:
                thread.join(timeout=3)


def _ingest_pids() -> list[int]:
    found = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\x00", b" ")
        except OSError:
            continue
        if b"pair.ingest.job" in command:
            found.append(int(entry.name))
    return found


class AttachmentIsolation(unittest.TestCase):
    """Real child. INLINE stays false so a deadline or a disconnect can kill it."""

    def setUp(self):
        self._inline = ingest_job.INLINE
        self._deadline = ingest_job.UPLOAD_DEADLINE_S
        self._sleep = os.environ.get("PI_PAIR_INGEST_TEST_SLEEP")
        self._peers = [dict(peer) for peer in runtime.PEERS]
        ingest_job.INLINE = False
        os.environ.pop("PI_PAIR_INGEST_TEST_SLEEP", None)
        runtime.set_peers([])
        self.httpd = make_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        ingest_job.INLINE = self._inline
        ingest_job.UPLOAD_DEADLINE_S = self._deadline
        if self._sleep is None:
            os.environ.pop("PI_PAIR_INGEST_TEST_SLEEP", None)
        else:
            os.environ["PI_PAIR_INGEST_TEST_SLEEP"] = self._sleep
        runtime.set_peers(self._peers)
        for pid in _ingest_pids():
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        self.httpd.shutdown()
        self.httpd.server_close()

    def _post(self, data: bytes, headers: dict[str, str], timeout: float = 5):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/attachments",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            return error.code, json.loads(raw or "{}")

    def _elapsed_get(self, path: str) -> float:
        started = time.monotonic()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=2
        ) as response:
            self.assertEqual(response.status, 200)
            response.read()
        return time.monotonic() - started

    def test_s0_upload_leaves_health_responsive(self):
        pdf = _s0_pdf()
        box: dict = {}

        def post() -> None:
            started = time.monotonic()
            box["result"] = self._post(
                pdf,
                {"content-type": "application/pdf", "x-filename": "s0.pdf"},
            )
            box["dt"] = time.monotonic() - started

        thread = threading.Thread(target=post)
        thread.start()
        samples = [self._elapsed_get("/health") for _ in range(20)]
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertLess(box["dt"], 5)
        status, body = box["result"]
        self.assertIn(status, (200, 422))
        if status == 200:
            self.assertEqual(body.get("text"), "Hello scan")
        self.assertTrue(all(sample < 0.3 for sample in samples), samples)

    def test_deadline_kills_the_child(self):
        ingest_job.UPLOAD_DEADLINE_S = 1
        os.environ["PI_PAIR_INGEST_TEST_SLEEP"] = "3"
        box: dict = {}

        def post() -> None:
            started = time.monotonic()
            box["result"] = self._post(
                JPEG,
                {"content-type": "image/jpeg", "x-filename": "pic.jpg"},
                timeout=4,
            )
            box["dt"] = time.monotonic() - started

        thread = threading.Thread(target=post)
        thread.start()
        pid = None
        limit = time.monotonic() + 2
        while time.monotonic() < limit and pid is None:
            found = _ingest_pids()
            if found:
                pid = found[0]
                break
            time.sleep(0.02)
        static_s = self._elapsed_get("/static/mesh.css")
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertLess(box["dt"], 2)
        status, body = box["result"]
        self.assertEqual(status, 504, body)
        self.assertEqual(body.get("error"), "That took too long. Try again.")
        self.assertIsNotNone(pid)
        self.assertTrue(_stopped(pid), pid)
        self.assertLess(static_s, 0.3)

    def test_disconnect_kills_the_child_and_releases_the_gate(self):
        os.environ["PI_PAIR_INGEST_TEST_SLEEP"] = "3"
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        body = JPEG
        head = (
            "POST /v1/attachments HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            "Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(body)}\r\n"
            "X-Filename: pic.jpg\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        sock.sendall(head + body)
        pid = None
        limit = time.monotonic() + 2
        while time.monotonic() < limit:
            found = _ingest_pids()
            if found:
                pid = found[0]
                break
            time.sleep(0.02)
        self.assertIsNotNone(pid)
        sock.close()
        self.assertTrue(_stopped(int(pid)))
        os.environ.pop("PI_PAIR_INGEST_TEST_SLEEP", None)
        status, _body = self._post(
            JPEG, {"content-type": "image/jpeg", "x-filename": "again.jpg"}
        )
        self.assertNotEqual(status, 429)

    def test_two_slow_uploads_reject_a_third(self):
        os.environ["PI_PAIR_INGEST_TEST_SLEEP"] = "3"
        threads = []
        try:
            for _ in range(2):
                thread = threading.Thread(
                    target=lambda: self._post(
                        JPEG,
                        {"content-type": "image/jpeg", "x-filename": "pic.jpg"},
                        timeout=8,
                    )
                )
                thread.start()
                threads.append(thread)
            limit = time.monotonic() + 2
            while time.monotonic() < limit and len(_ingest_pids()) < 2:
                time.sleep(0.02)
            self.assertGreaterEqual(len(_ingest_pids()), 2)
            started = time.monotonic()
            status, body = self._post(
                JPEG,
                {"content-type": "image/jpeg", "x-filename": "third.jpg"},
                timeout=2,
            )
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(status, 429, body)
        finally:
            os.environ.pop("PI_PAIR_INGEST_TEST_SLEEP", None)
            for thread in threads:
                thread.join(timeout=8)


def _stopped(pid: int) -> bool:
    """True once the pid is gone or only a zombie. A sleeping OCR child is neither."""
    for _ in range(50):
        status = Path(f"/proc/{pid}/status")
        if not status.exists():
            return True
        state = ""
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("State:"):
                state = line.split()[1]
                break
        if state in {"Z", "X"}:
            return True
        time.sleep(0.02)
    return False


def _as_post(part: tuple[str, bytes]) -> tuple[bytes, dict[str, str]]:
    mime, data = part
    return data, {"content-type": mime}


class AttachmentWire(unittest.TestCase):
    def test_readme_and_install_name_pi_deps(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("tesseract-ocr", install)
        self.assertIn("poppler-utils", install)
        self.assertIn('cp -a "$ROOT/pair" "$INSTALL_DIR/pair.new"', install)

    def test_composer_posts_then_sends_the_text(self):
        source = web_source()
        self.assertIn('"/v1/attachments"', source)
        self.assertIn("XMLHttpRequest", source)
        self.assertIn("onprogress", source)
        self.assertIn("uploading", source)
        self.assertIn("ATTACH_BYTES = 4 * 1024 * 1024", source)
        self.assertIn("dataset.attachText", source)
        self.assertIn("modelUserContent", source)
        attached = web_source()
        self.assertIn('"\\n\\n---\\n"', attached)
        self.assertNotIn("readAsText", source)
        html = (ROOT / "web" / "public" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="btnAttach"', html)
        self.assertIn("application/pdf", html)
        self.assertIn("image/jpeg", html)
        self.assertIn(".txt", html)
        self.assertIn(".md", html)
        lowered = html.lower()
        for word in ("cache", "brain", "chip", "peer", "pi2", "pi3", "pi4"):
            self.assertNotIn(word, lowered, word)
        built = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("application/pdf", built)
        script = (ROOT / "static" / "mesh.js").read_text(encoding="utf-8")
        self.assertIn("/v1/attachments", script)
        self.assertNotIn("readAsText", script)


if __name__ == "__main__":
    unittest.main()
