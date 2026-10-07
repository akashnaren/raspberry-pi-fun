"""Structural checks for the ten chat abilities. No stored answers."""

from __future__ import annotations

import unittest

from pair.render.charts import render_chart
from pair.render.documents import render_document
from pair.server import Handler
from pair.turn.abilities import (
    CATEGORIES,
    category_rates,
    clean_reply,
    mend_cut_tail,
    parse_fences,
    tool_notes,
)
from pair.turn.shape import PERSONA


class Abilities(unittest.TestCase):
    def test_each_ability_lifts_only_its_category(self):
        everything = category_rates()
        for name in CATEGORIES:
            self.assertGreaterEqual(everything[name], 0.9, name)
        for name in CATEGORIES:
            off = category_rates(set(CATEGORIES) - {name})
            self.assertGreater(everything[name], off[name], name)
            for other in CATEGORIES:
                if other == name:
                    continue
                self.assertEqual(everything[other], off[other], other)

    def test_a_calc_fence_asks_the_model_once_more(self):
        calls = []

        class Dummy:
            _extra_call = False

            def _decode_reply(self, *args, **kwargs):
                calls.append(args)
                return "equals 4", "m", True

        reply = "```calc\n2+2\n```"
        out = Handler._one_more_round(
            Dummy(), None, "ollama", "m", [], 0.3, 96, "add", None, reply
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("```calc", out)
        self.assertIn("equals 4", out)
        note = calls[0][3][-1]["content"]
        self.assertIn("Calculator: 2+2 = 4", note)
        self.assertEqual(len(tool_notes(reply, search=lambda _q: {})), len(note))

    def test_plain_text_does_not_take_a_second_call(self):
        class Dummy:
            _extra_call = False

            def _decode_reply(self, *args, **kwargs):
                raise AssertionError("second call")

        out = Handler._one_more_round(
            Dummy(), None, "ollama", "m", [], 0.3, 96, "hi", None, "hello"
        )
        self.assertEqual(out, "hello")

    def test_clean_reply_drops_echoes_empty_fences_and_stray_markers(self):
        echoed = next(
            line
            for line in PERSONA.splitlines()
            if line and not line.startswith("```") and not line.startswith("|")
        )
        question = "What is the capital of Australia?"
        raw = "\n".join(
            [
                "Canberra.",
                echoed,
                question,
                "```calc",
                "```",
                "```not a language",
                "junk",
                "```",
                "```doc",
                "kind: txt",
                "Hi",
                "```",
                "```doc",
                "kind: txt",
                "Hi",
                "```",
                "See [n] and [n=1] and [3].",
            ]
        )
        cleaned = clean_reply(raw, question, source_count=0)
        self.assertIn("Canberra.", cleaned)
        self.assertNotIn(echoed, cleaned)
        self.assertNotIn(question, cleaned)
        self.assertNotIn("```calc", cleaned)
        self.assertNotIn("not a language", cleaned)
        self.assertEqual(cleaned.count("```doc"), 1)
        self.assertNotIn("[n]", cleaned)
        self.assertNotIn("[3]", cleaned)
        self.assertNotIn("[1]", cleaned)
        file_q = "Make a txt file with a greeting."
        file_raw = "\n".join(
            [
                "```doc",
                "kind: txt",
                "Hi",
                "```",
                "```doc",
                "kind: txt",
                "Hi",
                "```",
            ]
        )
        file_cleaned = clean_reply(file_raw, file_q)
        self.assertEqual(file_cleaned.count("```doc"), 1)
        self.assertIn("Hi", file_cleaned)

    def test_pdf_xlsx_and_md_fences_become_files(self):
        def rendered(body: str):
            kind = "txt"
            lines = []
            for line in body.splitlines():
                if not lines and line.lower().startswith("kind:"):
                    kind = line.split(":", 1)[1].strip().lower()
                    continue
                lines.append(line)
            return render_document("\n".join(lines).strip(), kind)

        pdf = parse_fences("```pdf\nQuarter notes\n```")
        xlsx = parse_fences("```xlsx\n| a | b |\n| --- | --- |\n| 1 | 2 |\n```")
        md = parse_fences("```md\n# Note\nHello\n```")
        chart = parse_fences("```chart\n| item | n |\n| --- | --- |\n| a | 1 |\n```")
        self.assertEqual([item["name"] for item in pdf], ["doc"])
        self.assertEqual(xlsx[0]["name"], "doc")
        self.assertEqual(md[0]["name"], "doc")
        self.assertEqual(chart[0]["name"], "plot")
        pdf_bytes, pdf_ext = rendered(pdf[0]["body"])
        xlsx_bytes, xlsx_ext = rendered(xlsx[0]["body"])
        md_bytes, md_ext = rendered(md[0]["body"])
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(pdf_ext, "pdf")
        self.assertIn(b"Quarter notes", pdf_bytes)
        self.assertTrue(xlsx_bytes.startswith(b"PK"))
        self.assertEqual(xlsx_ext, "xlsx")
        self.assertIn(b"Hello", md_bytes)
        self.assertEqual(md_ext, "md")
        drawn = render_chart(table=chart[0]["body"])
        self.assertTrue(drawn["ok"], drawn)

    def test_plotly_json_becomes_a_plot_fence(self):
        figure = (
            '{"data":[{"type":"bar","x":["1","2"],"y":[1,2],"name":"Sample Data"}],'
            '"layout":{"title":"Sample Data","xaxis":{"title":"Value"}}}'
        )
        cleaned = clean_reply(
            "Sure!\n```json\n" + figure + "\n```",
            "Try using plotly?",
        )
        self.assertIn("```plot", cleaned)
        self.assertNotIn("```json", cleaned)
        plain = clean_reply('```json\n{"name":"x"}\n```', "name a file")
        self.assertIn("```json", plain)
        self.assertIn('{"name":"x"}', plain)

    def test_a_cut_reply_loses_the_dangling_marker(self):
        cut = mend_cut_tail("- **Value 1:** 1\n- **Value 2", True)
        self.assertEqual(cut, "- **Value 1:** 1\n- Value 2…")
        self.assertEqual(cut.count("**") % 2, 0)
        self.assertEqual(
            mend_cut_tail("fine **bold** text.", False), "fine **bold** text."
        )
        fenced = "```python\ndef f(**kwargs):\n    return 1\n```"
        self.assertEqual(mend_cut_tail(fenced, False), fenced)
        self.assertIn("**kwargs", mend_cut_tail(fenced, True))

    def test_persona_names_the_downloadable_types(self):
        for kind in ("pdf", "docx", "xlsx", "md", "txt", "csv"):
            self.assertIn(kind, PERSONA)
        self.assertIn("plot fence", PERSONA)
        self.assertNotIn("pptx", PERSONA.lower())


if __name__ == "__main__":
    unittest.main()
