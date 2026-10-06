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
from pair.charts import render_chart, with_headers
from pair.compact import run_compact
from pair.docs import render_document
from pair.memory import facts_block, remember_user, save_summary, summary_text
from pair.server import Handler
from pair.turn import PERSONA, needs_web, sample_turns, shape_messages, turns_for_memory

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

    def test_a_copied_prompt_block_is_dropped_and_does_not_run_tools(self):
        copied = "```doc\n" + PERSONA + "\n```"
        cleaned = clean_reply(copied + "\nCanberra.", CAPITAL)
        self.assertIn("Canberra.", cleaned)
        self.assertNotIn("```", cleaned)
        self.assertNotIn(PERSONA, cleaned)
        calls = []

        def search(query):
            calls.append(query)
            return {"context": "no"}

        self.assertEqual(tool_notes(copied, search=search, prompt=CAPITAL), "")
        self.assertEqual(calls, [])

        class Dummy:
            _extra_call = False

            def _decode_reply(self, *args, **kwargs):
                raise AssertionError("second call")

        out = Handler._one_more_round(
            Dummy(), None, "ollama", "m", [], 0.3, 96, CAPITAL, None, copied
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
    def test_a_file_type_word_does_not_make_a_download(self):
        prose = "DOCX is editable. PDF is a fixed page."
        for prompt in (
            "What is the difference between docx and pdf?",
            "Summarize this PDF in two lines.",
            DOCX,
        ):
            settled = settle_blocks(prose, prompt)
            self.assertEqual(settled, prose, prompt)
            self.assertNotIn("```", clean_reply(prose, prompt), prompt)
        mentioned = settle_blocks(
            "```doc\nkind: pdf\nVisit the museum.\n```",
            "What is the difference between docx and pdf?",
        )
        self.assertIn("kind: pdf", mentioned)
        self.assertIn("Visit the museum.", mentioned)

    def test_the_model_kind_and_table_are_kept(self):
        pdf = settle_blocks(
            "```doc\nkind: pdf\ntitle: Seattle Weekend\nVisit the museum.\n```",
            "What is the difference between docx and pdf?",
        )
        self.assertIn("kind: pdf", pdf)
        self.assertNotIn("kind: docx", pdf)
        markdown = settle_blocks(
            "```markdown\nkind: md\nMix flour and milk.\n```",
            XLSX,
        )
        self.assertIn("```markdown", markdown)
        self.assertIn("kind: md", markdown)
        self.assertNotIn("kind: xlsx", markdown)
        self.assertIn("Mix flour", markdown)
        grades = "| grade | score |\n| --- | --- |\n| A | 90 |\n| B | 80 |\n| C | 70 |"
        sheet = settle_blocks("```xlsx\n" + grades + "\n```", XLSX)
        self.assertIn("| A | 90 |", sheet)
        self.assertIn("| B | 80 |", sheet)
        self.assertIn("| C | 70 |", sheet)
        self.assertNotIn("Jan", sheet)
        self.assertNotIn("| label | value |", sheet)

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
            "```doc\nkind: pdf\ntitle: Seattle Weekend\n"
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

    def test_charts_use_the_model_table_and_headerless_rows_get_label_value(self):
        table = (
            "| Month | Sales |\n| --- | --- |\n| Jan | 12 |\n| Feb | 99 |\n| Mar | 9 |"
        )
        settled = settle_blocks("```chart\n" + table + "\n```", CHART)
        self.assertIn("| Feb | 99 |", settled)
        self.assertNotIn("| Feb | 18 |", settled)
        drawn = render_chart(table=_body(parse_fences(settled)[0]))
        self.assertTrue(drawn["ok"], drawn)
        self.assertEqual(drawn["figure"]["data"][0]["y"], [12, 99, 9])
        self.assertEqual(drawn["figure"]["data"][0]["name"], "Sales")
        bare = "| A | 90 |\n| B | 80 |\n| C | 70 |"
        headed = with_headers(bare)
        self.assertIn("| label | value |", headed)
        self.assertIn("| A | 90 |", headed)
        self.assertIn("| C | 70 |", headed)
        chart = settle_blocks(
            "```chart\n" + bare + "\n```",
            "Make an xlsx of grades: A 90, B 80, C 70.",
        )
        self.assertIn("| A | 90 |", chart)
        self.assertIn("| B | 80 |", chart)
        self.assertIn("| C | 70 |", chart)
        self.assertIn("| label | value |", chart)
        drawn = render_chart(table=_body(parse_fences(chart)[0]))
        self.assertEqual(drawn["figure"]["data"][0]["y"], [90, 80, 70])
        self.assertEqual(drawn["figure"]["data"][0]["x"], ["A", "B", "C"])
        bar = (
            "```bar\n| month | sales |\n| --- | --- |\n"
            "| Jan | 12 |\n| Feb | 18 |\n| Mar | 9 |\n| Apr | 15 |\n```"
        )
        kept = settle_blocks(bar, CHART)
        drawn = render_chart(table=_body(parse_fences(kept)[0]))
        self.assertEqual(drawn["figure"]["data"][0]["y"], [12, 18, 9, 15])
        copied = next(
            row["content"]
            for row in sample_turns()
            if row["content"].startswith("```plot")
        )
        self.assertNotIn("north", clean_reply(copied, CHART))
        open_md = "```md\n# Pancakes\nMix flour and milk.\n"
        closed = settle_blocks(open_md, MD)
        self.assertEqual(parse_fences(closed)[0]["name"], "doc")
        self.assertIn("Mix flour and milk.", closed)


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
            self.assertTrue(needs_web(cactus, follow_up=True, context=context))
        tool_notes(
            "```search\ncactus watering\n```",
            search=search,
            prompt=cactus,
            context=context,
        )
        self.assertEqual(calls, ["cactus watering"])
        calls.clear()
        tool_notes(
            "```search\nsuper bowl winner\n```\n```search\nagain\n```",
            search=search,
            prompt="Who won the most recent Super Bowl?",
        )
        self.assertEqual(calls, ["super bowl winner"])
        calls.clear()
        chained = "What is 15% of 240, then add 12?"
        notes = tool_notes(
            "```calc\n0.15*240+12\n```\n```search\n15 percent of 240\n```",
            search=search,
            prompt=chained,
        )
        self.assertIn("0.15*240+12 = 48", notes)
        self.assertNotIn(
            "```search",
            settle_blocks(
                "```calc\n0.15*240+12\n```\n```search\n15 percent of 240\n```",
                chained,
            ),
        )
        self.assertEqual(calls, [])
        self.assertFalse(needs_web(chained))


if __name__ == "__main__":
    unittest.main()
