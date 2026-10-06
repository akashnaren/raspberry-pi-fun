"""Charts from table shape, and documents that are real Office and PDF files."""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from pair.charts import chart_samples, chart_type, render_chart
from pair.docs import open_document, purge_documents, render_document, save_document
from pair.nodes.worker import forbidden_routes, handle
from pair.server import make_server


class Charts(unittest.TestCase):
    def test_shape_picks_the_chart(self):
        bar = "| item | value |\n| --- | --- |\n| a | 2 |\n| b | 5 |\n"
        line = (
            "| day | value |\n| --- | --- |\n| 2024-01-01 | 3 |\n| 2024-01-02 | 4 |\n"
        )
        ordered = "| x | y |\n| --- | --- |\n| 1 | 2 |\n| 3 | 1 |\n"
        scatter = "| x | y |\n| --- | --- |\n| 1 | 2 |\n| 3 | 1 |\n| 2 | 4 |\n"
        self.assertEqual(chart_type(*_table(bar)), "bar")
        self.assertEqual(chart_type(*_table(line)), "line")
        self.assertEqual(chart_type(*_table(ordered)), "line")
        self.assertEqual(chart_type(*_table(scatter)), "scatter")
        forced = render_chart(table="type: bar\ntitle: Items\n" + line)
        self.assertEqual(forced["type"], "bar")
        self.assertEqual(forced["figure"]["layout"]["title"], "Items")
        self.assertIn(line.strip().split("\n")[0], forced["table"])

    def test_a_plot_fence_uses_the_safe_evaluator(self):
        drawn = render_chart(plot="title: Wave\nsin(x)")
        self.assertTrue(drawn["ok"])
        self.assertEqual(drawn["type"], "line")
        self.assertGreaterEqual(len(drawn["figure"]["data"][0]["x"]), 2)
        missed = render_chart(plot="open(__import__('os').system)")
        self.assertFalse(missed["ok"])
        self.assertIn("open", missed["table"])

    def test_most_sample_tables_render_and_the_rest_stay_text(self):
        samples = chart_samples()
        rendered = 0
        fallback = 0
        for sample in samples:
            result = render_chart(table=sample["table"])
            if result["ok"] and result["type"] == sample["expect"]:
                rendered += 1
            elif sample["table"] in result["table"]:
                fallback += 1
        self.assertEqual(len(samples), 50)
        self.assertGreaterEqual(rendered / len(samples), 0.9)
        self.assertEqual(rendered + fallback, 50)
        self.assertGreater(fallback, 0)

    def test_the_worker_chart_route_does_not_generate(self):
        self.assertEqual(forbidden_routes(), [])
        status, body = handle(
            "/tools/render_chart",
            {"table": "| item | value |\n| --- | --- |\n| a | 1 |\n| b | 2 |\n"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["type"], "bar")
        self.assertNotIn("tokens", body)


def _table(text: str):
    from pair.charts import parse_markdown_table

    parsed = parse_markdown_table(text)
    assert parsed is not None
    return parsed


class Documents(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("PI_PAIR_DATA")
        os.environ["PI_PAIR_DATA"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PI_PAIR_DATA", None)
        else:
            os.environ["PI_PAIR_DATA"] = self._old
        self._tmp.cleanup()

    def test_markdown_becomes_docx_xlsx_and_pdf(self):
        source = "Hello mesh\n\n| item | value |\n| --- | --- |\n| a | 2 |\n"
        docx, ext = render_document(source, "docx")
        self.assertEqual(ext, "docx")
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("Hello mesh", xml)
        xlsx, ext = render_document(source, "xlsx")
        self.assertEqual(ext, "xlsx")
        with zipfile.ZipFile(io.BytesIO(xlsx)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("item", sheet)
        self.assertIn("value", sheet)
        pdf, ext = render_document(source, "pdf")
        self.assertEqual(ext, "pdf")
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"Hello mesh", pdf)
        text, ext = render_document(source, "txt")
        self.assertEqual(ext, "txt")
        self.assertIn("Hello mesh", text.decode())
        csv_bytes, ext = render_document(source, "csv")
        self.assertEqual(ext, "csv")
        self.assertIn("item,value", csv_bytes.decode())

    def test_files_are_deleted_after_a_day(self):
        data, ext = render_document("keep me\n", "txt")
        meta = save_document(data, ext, "note")
        self.assertIsNotNone(open_document(meta["id"], now=meta["created"] + 86399))
        self.assertGreaterEqual(purge_documents(now=meta["created"] + 86401), 1)
        self.assertIsNone(open_document(meta["id"], now=meta["created"] + 86401))

    def test_worker_returns_a_downloadable_file(self):
        status, body = handle(
            "/tools/render_doc",
            {"markdown": "Hello mesh\n", "kind": "pdf", "name": "note"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["kind"], "pdf")
        self.assertTrue(base64.b64decode(body["data"]).startswith(b"%PDF"))
        found = open_document(body["id"])
        self.assertIsNotNone(found)
        self.assertEqual(found[0][:4], b"%PDF")

    def test_libreoffice_opens_the_office_files(self):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            self.skipTest("LibreOffice is not installed")
        source = "Hello mesh\n\n| item | value |\n| --- | --- |\n| a | 2 |\n"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for kind, target in (("docx", "txt"), ("xlsx", "csv"), ("pdf", "png")):
                data, ext = render_document(source, kind)
                src = root / f"note.{ext}"
                src.write_bytes(data)
                out = root / kind
                out.mkdir()
                proc = subprocess.run(
                    [
                        soffice,
                        "--headless",
                        "--convert-to",
                        target,
                        "--outdir",
                        str(out),
                        str(src),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=120,
                )
                self.assertEqual(
                    proc.returncode, 0, proc.stderr.decode("utf-8", "replace")
                )
                converted = next(out.glob(f"note.{target}"))
                if kind == "pdf":
                    self.assertTrue(converted.read_bytes().startswith(b"\x89PNG"))
                    self.assertIn(b"Hello mesh", data)
                    continue
                opened = converted.read_text(encoding="utf-8", errors="replace")
                self.assertIn("item", opened)
                if kind == "docx":
                    self.assertIn("Hello mesh", opened)


class BrainRender(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("PI_PAIR_DATA")
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._forward = os.environ.get("PI_PAIR_TOOL_FORWARD")
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ.pop("PI_PAIR_TOOL_FORWARD", None)
        self._httpd = make_server("127.0.0.1", 0)
        self._port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._old is None:
            os.environ.pop("PI_PAIR_DATA", None)
        else:
            os.environ["PI_PAIR_DATA"] = self._old
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role
        if self._forward is None:
            os.environ.pop("PI_PAIR_TOOL_FORWARD", None)
        else:
            os.environ["PI_PAIR_TOOL_FORWARD"] = self._forward
        self._tmp.cleanup()

    def _post(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._port}{path}",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode() or "{}")

    def test_brain_renders_a_chart_and_still_refuses_search(self):
        status, body = self._post("/tools/search", {"q": "bench"})
        self.assertEqual(status, 403)
        status, body = self._post(
            "/tools/render_chart",
            {"table": "| item | value |\n| --- | --- |\n| a | 1 |\n"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["type"], "bar")
        self.assertNotIn("tokens", body)
        status, body = self._post(
            "/tools/render_doc",
            {"markdown": "Hello mesh\n", "kind": "txt", "name": "note"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["kind"], "txt")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self._port}/v1/files/{body['id']}", timeout=5
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Hello mesh", response.read())


if __name__ == "__main__":
    unittest.main()
