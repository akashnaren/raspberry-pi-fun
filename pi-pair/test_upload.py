"""MIME routing for attachment ingest. OCR is mocked; no tesseract in CI."""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import ocr, upload
from pair.server import make_server

JPEG = b"\xff\xd8\xff\xd9"
JPEG_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /XObject /Subtype /Image /Filter /DCTDecode /Length 4 >> stream\n"
    + JPEG
    + b"\nendstream endobj\n%%EOF\n"
)
TEXT_PDF = b"%PDF-1.4\n1 0 obj << /Length 5 >> stream\n(Hi)\nendstream endobj\n%%EOF\n"


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
        self.assertIn("JPEG-scanned", str(pdf_error.exception))
        self.assertFalse(upload.is_jpeg_scanned_pdf(TEXT_PDF))
        self.assertTrue(upload.is_jpeg_scanned_pdf(JPEG_PDF))
        with self.assertRaises(upload.UploadRejected) as csv_error:
            upload.route_for("rows.csv", "text/csv", b"a,b")
        self.assertEqual(csv_error.exception.status, 415)
        self.assertEqual(str(csv_error.exception), "unsupported file type")

    def test_txt_extension_stays_text_and_jpeg_named_pdf_is_an_image(self):
        self.assertEqual(upload.route_for("notes.txt", "application/octet-stream", JPEG), "text")
        self.assertEqual(upload.route_for("scan.pdf", "application/pdf", JPEG), "ocr")
        self.assertEqual(upload.ocr_kind("scan.pdf", "application/pdf", JPEG), "image")
        self.assertEqual(upload.ocr_kind("scan.pdf", "application/pdf", JPEG_PDF), "pdf")

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
        blob = " ".join(image + pdf)
        self.assertNotIn("http", blob)
        self.assertNotIn("://", blob)
        with self.assertRaises(ocr.OcrNotInstalled):
            ocr.run_local(["__pi_pair_no_such_ocr_bin__"], b"")
        with self.assertRaises(ocr.OcrFailed):
            ocr.run_local([sys.executable, "-c", "import sys; sys.exit(2)"])


class AttachmentHttp(unittest.TestCase):
    def setUp(self):
        self.httpd = make_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
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
        status, body = self._post(*_as_post(_multipart("notes.txt", "text/plain", b"alpha")))
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "text")
        self.assertEqual(body["text"], "alpha")
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

        status, body = self._post(*_as_post(_multipart("../notes.md", "text/markdown", b"# beta")))
        self.assertEqual(status, 200)
        self.assertEqual(body["name"], "notes.md")
        self.assertEqual(body["text"], "# beta")
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

        status, body = self._post(*_as_post(_multipart("pic.jpg", "image/jpeg", JPEG)))
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "ocr")
        self.assertEqual(body["text"], "from image")
        self.assertEqual(self.calls, {"image": 1, "pdf": 0})

        status, body = self._post(*_as_post(_multipart("scan.pdf", "application/pdf", JPEG_PDF)))
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "ocr")
        self.assertEqual(body["text"], "from pdf")
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
        status, body = self._post(*_as_post(_multipart("essay.pdf", "application/pdf", TEXT_PDF)))
        self.assertEqual(status, 415)
        self.assertIn("JPEG-scanned", body["error"])
        self.assertEqual(self.calls, {"image": 0, "pdf": 0})

        status, body = self._post(*_as_post(_multipart("rows.csv", "text/csv", b"a,b")))
        self.assertEqual(status, 415)
        self.assertEqual(body["error"], "unsupported file type")

        previous = upload.MAX_UPLOAD_BYTES
        upload.MAX_UPLOAD_BYTES = 32
        try:
            status, body = self._post(b"y" * 40, {"content-type": "text/plain", "x-filename": "notes.txt"})
        finally:
            upload.MAX_UPLOAD_BYTES = previous
        self.assertEqual(status, 413)
        self.assertEqual(body["error"], "attachment is over 4 MB")


def _as_post(part: tuple[str, bytes]) -> tuple[bytes, dict[str, str]]:
    mime, data = part
    return data, {"content-type": mime}


class AttachmentWire(unittest.TestCase):
    def test_readme_and_install_name_pi_deps(self):
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("tesseract-ocr", readme)
        self.assertIn("poppler-utils", readme)
        self.assertIn("pdftoppm", readme)
        self.assertIn("/v1/attachments", readme)
        self.assertIn("4 MB", readme)
        self.assertIn("4096", readme)
        self.assertIn("no cloud OCR API", readme.lower())
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("tesseract-ocr", install)
        self.assertIn("poppler-utils", install)

    def test_composer_posts_then_sends_the_text(self):
        source = (ROOT / "web" / "src" / "main.ts").read_text(encoding="utf-8")
        self.assertIn('fetch("/v1/attachments"', source)
        self.assertIn("ATTACH_BYTES = 4 * 1024 * 1024", source)
        self.assertIn("dataset.attachText", source)
        self.assertIn('"\\n\\n---\\n"', source)
        self.assertNotIn("readAsText", source)
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
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
