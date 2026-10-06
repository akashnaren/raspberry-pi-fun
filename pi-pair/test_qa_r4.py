"""Live QA bugs: copied fences, trimmed memory, and file or chart contents."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pair.abilities import clean_reply, parse_fences, settle_blocks, tool_notes
from pair.charts import render_chart
from pair.compact import run_compact
from pair.docs import render_document
from pair.memory import facts_block, remember_user, save_summary, summary_text
from pair.server import Handler
from pair.turn import needs_web, shape_messages, turns_for_memory

EXAMPLE = (
    "```doc\nkind: pdf\ntitle: Note\n"
    "| item | n |\n| --- | --- |\n| a | 1 |\n"
    "A short paragraph.\n```"
)
CAPITAL = "What is the capital of Australia?"
CHART = "Make a bar chart of monthly sales: Jan 12, Feb 18, Mar 9, Apr 15."
XLSX = (
    "Make an XLSX spreadsheet I can download with columns Month and Sales: "
    "Jan 12, Feb 18, Mar 9."
)
PDF = (
    "Make a PDF file I can download: a one-page note titled Seattle Weekend "
    "with three things to do."
)
DOCX = (
    "Make a DOCX file I can download: a packing list for a beach trip with five items."
)
MD = "Make a Markdown .md file I can download with a short recipe for pancakes."


def _body(fence: dict) -> str:
    lines = []
    for line in fence["body"].splitlines():
        if not lines and line.lower().startswith(("kind:", "title:")):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


class ExampleFence(unittest.TestCase):
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

    def test_a_copied_example_is_dropped_and_does_not_run_tools(self):
        cleaned = clean_reply(EXAMPLE + "\nCanberra.", CAPITAL)
        self.assertIn("Canberra.", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertNotIn("| a | 1 |", cleaned)
        self.assertNotIn("A short paragraph.", cleaned)
        calls = []

        def search(query):
            calls.append(query)
            return {"context": "no"}

        self.assertEqual(tool_notes(EXAMPLE, search=search, prompt=CAPITAL), "")
        self.assertEqual(calls, [])

        class Dummy:
            _extra_call = False

            def _decode_reply(self, *args, **kwargs):
                raise AssertionError("second call")

        out = Handler._one_more_round(
            Dummy(), None, "ollama", "m", [], 0.3, 96, CAPITAL, None, EXAMPLE
        )
        self.assertNotIn("```", out)
        self.assertEqual(list(Path(self._tmp.name).glob("docs/*")), [])


class LockerMemory(unittest.TestCase):
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

    def test_locker_code_survives_trimming_and_is_recalled(self):
        turns = [
            {"role": "user", "content": "My locker code is 4417."},
            {"role": "assistant", "content": "Noted."},
        ]
        filler = "The afternoon weather stayed mild and cloudy over the coast. " * 8
        for index in range(8):
            turns.append({"role": "user", "content": f"{filler} day {index}"})
            turns.append({"role": "assistant", "content": "Noted the weather."})
        shaped = shape_messages(turns, turns[-1]["content"], knobs={"num_ctx": 512})
        shaped_blob = "\n".join(str(row["content"]) for row in shaped)
        self.assertNotIn("4417", shaped_blob)
        lost = run_compact(
            shaped,
            512,
            idle=lambda: True,
            generate=lambda _draft: "weather only",
        )
        self.assertNotIn("4417", lost["summary"])
        self.assertNotIn("4417", "\n".join(lost["facts"]))
        kept = run_compact(
            turns_for_memory(turns, shaped),
            512,
            idle=lambda: True,
            generate=lambda _draft: "weather only",
        )
        self.assertIn("4417", kept["summary"])
        remember_user(kept["facts"])
        save_summary(kept["summary"], kept["elapsed_ms"])
        asked = "What is my locker code?"
        recall = shape_messages(
            [{"role": "user", "content": asked}],
            asked,
            facts=facts_block(),
            summary=summary_text(),
        )
        blob = "\n".join(str(row["content"]) for row in recall)
        self.assertIn("4417", blob)
        self.assertIn("The user said:", blob)


class FileAndChart(unittest.TestCase):
    def test_file_kind_comes_from_the_request(self):
        pdf = settle_blocks(
            "```doc\ntitle: Seattle Weekend\nVisit the museum.\n```",
            PDF,
        )
        self.assertIn("kind: pdf", pdf)
        self.assertNotIn("docx", pdf)
        markdown = settle_blocks(
            "```markdown\nkind: docx\nMix flour and milk.\n```",
            MD,
        )
        self.assertIn("```doc", markdown)
        self.assertIn("kind: md", markdown)
        self.assertNotIn("kind: docx", markdown)
        self.assertNotIn("```markdown", markdown)
        self.assertIn("Mix flour", markdown)
        echoed = settle_blocks(
            "```markdown\nkind: docx\n| item | n |\n| --- | --- |\n| a | 1 |\n```",
            MD,
        )
        self.assertNotIn("| a | 1 |", echoed)
        self.assertNotIn("kind: docx", echoed)
        prose = "Packing list: 1. Sunscreen 2. Towel 3. Sunhat 4. Sandals 5. Water."
        wrapped = settle_blocks(prose, DOCX)
        self.assertIn("kind: docx", wrapped)
        self.assertIn("Sunscreen", wrapped)
        self.assertEqual(settle_blocks(prose, CAPITAL), prose)

    def test_docx_and_pdf_turn_markdown_tables_into_tables(self):
        docx = settle_blocks(
            "```doc\nkind: docx\ntitle: Packing List\n"
            "| item | n |\n| --- | --- |\n| beach towel | 1 |\n| sunscreen | 1 |\n```",
            DOCX,
        )
        fence = parse_fences(docx)[0]
        data, ext = render_document(_body(fence), "docx")
        self.assertEqual(ext, "docx")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("<w:tbl>", xml)
        self.assertIn("beach towel", xml)
        self.assertNotIn("| beach towel |", xml)
        pdf = settle_blocks(
            "```doc\ntitle: Seattle Weekend\n"
            "| item | n |\n| --- | --- |\n"
            "| 1 | Visit the museum |\n```",
            PDF,
        )
        self.assertIn("kind: pdf", pdf)
        fence = parse_fences(pdf)[0]
        data, ext = render_document(_body(fence), "pdf")
        self.assertEqual(ext, "pdf")
        self.assertIn(b"Visit the museum", data)
        self.assertIn(b" m ", data)
        self.assertNotIn(b"| Visit the museum |", data)

    def test_xlsx_and_charts_use_the_user_numbers(self):
        alphabet = "\n".join(
            f"| {chr(97 + index)} | {index + 1} |" for index in range(26)
        )
        sheet = settle_blocks(
            "```xlsx\n| item | n |\n| --- | --- |\n" + alphabet + "\n```",
            XLSX,
        )
        self.assertIn("kind: xlsx", sheet)
        self.assertIn("Month", sheet)
        self.assertIn("Sales", sheet)
        self.assertIn("Jan", sheet)
        self.assertNotIn("| a | 1 |", sheet)
        fence = parse_fences(sheet)[0]
        data, ext = render_document(_body(fence), "xlsx")
        self.assertEqual(ext, "xlsx")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Month", xml)
        self.assertIn("Sales", xml)
        self.assertIn("Jan", xml)
        self.assertIn("12", xml)
        drawn = render_chart(
            table=_body(parse_fences(settle_blocks(EXAMPLE, CHART))[0])
        )
        self.assertTrue(drawn["ok"], drawn)
        self.assertEqual(drawn["figure"]["data"][0]["y"], [12, 18, 9, 15])
        self.assertNotIn("| a | 1 |", settle_blocks(EXAMPLE, CHART))


class LocalAnswers(unittest.TestCase):
    def test_arithmetic_and_memory_skip_search(self):
        prompts = (
            "What is 1234 + 5678 - 999?",
            "What is 15% of 2340?",
        )
        for knobs in ({"ground_all": True}, {"ground_all": False}):
            with patch("pair.turn.inference_knobs", return_value=knobs):
                for prompt in prompts:
                    self.assertFalse(needs_web(prompt), prompt)
                self.assertTrue(needs_web("Who won the most recent Super Bowl?"))
        calls = []

        def search(query):
            calls.append(query)
            return {"context": "notes"}

        calc = "What is 1234 + 5678 - 999?"
        self.assertIn("5913", clean_reply("```search\naddition\n```", calc))
        tool_notes("```search\naddition\n```", search=search, prompt=calc)
        percent = "What is 15% of 2340?"
        self.assertIn("351", clean_reply("```search\npercent\n```", percent))
        tool_notes("```search\npercent\n```", search=search, prompt=percent)
        cactus = "How often should I water the cactus?"
        context = "The user said: water the cactus every Sunday morning."
        with patch("pair.turn.inference_knobs", return_value={"ground_all": True}):
            self.assertTrue(needs_web(cactus, follow_up=True))
            self.assertFalse(needs_web(cactus, follow_up=True, context=context))
        tool_notes(
            "```search\ncactus watering\n```",
            search=search,
            prompt=cactus,
            context=context,
        )
        self.assertEqual(calls, [])
        tool_notes(
            "```search\nsuper bowl winner\n```",
            search=search,
            prompt="Who won the most recent Super Bowl?",
        )
        self.assertEqual(calls, ["super bowl winner"])


if __name__ == "__main__":
    unittest.main()
