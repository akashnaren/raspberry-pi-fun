"""Charts, lists, document excerpts, and the Pro preload."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.charts import (  # noqa: E402
    CHART_FALLBACK,
    chart_json_ok,
    is_structured_request,
    parabola_chart,
    repair_chart_reply,
    structure_hint,
)
from pair.docfit import DOC_FIT_CHARS, excerpt_limit, fit_document, fit_outbound  # noqa: E402
from pair.preload import (  # noqa: E402
    PRELOAD_TIMEOUT_S,
    pro_preload_payload,
    rewarm_pro_if_evicted,
)


class Charts(unittest.TestCase):
    def test_parabola_is_a_chart_fence(self):
        fence = parabola_chart("Plot me a parabolic curve")
        self.assertIsNotNone(fence)
        spec = json.loads(fence.split("\n", 1)[1].rsplit("\n", 1)[0])
        series = spec["data"][0]
        self.assertEqual(series["type"], "scatter")
        self.assertEqual(series["y"], [25, 16, 9, 4, 1, 0, 1, 4, 9, 16, 25])
        self.assertIsNone(structure_hint("Plot me a parabolic curve"))

    def test_other_plots_skip_search_and_keep_a_hint(self):
        self.assertTrue(is_structured_request("graph the temperature this week"))
        self.assertIsNone(parabola_chart("graph the temperature this week"))
        hint = structure_hint("graph the temperature this week")
        self.assertIn("```chart", hint)
        self.assertIn("Do not mention Desmos", hint)
        self.assertTrue(is_structured_request("make a table of name and year"))
        self.assertIn("```table", structure_hint("make a table of name and year"))
        self.assertTrue(is_structured_request("draw a flowchart of the login steps"))
        self.assertIn("mermaid", structure_hint("draw a flowchart of the login steps"))
        self.assertFalse(is_structured_request("where is the hall bench"))

    def test_valid_chart_is_kept_and_invalid_retries_once(self):
        prompt = "plot a bar chart of the fruit stand"
        good = 'Apples lead.\n```chart\n{"title":"Fruit","data":[{"type":"bar","y":[1,2]}]}\n```'
        calls = {"n": 0}

        def retry():
            calls["n"] += 1
            return good

        self.assertTrue(
            chart_json_ok('{"title":"Fruit","data":[{"type":"pie","values":[1,2]}]}')
        )
        self.assertFalse(chart_json_ok('{"data":[{"type":"bar","points":[1,2]}]}'))
        self.assertEqual(repair_chart_reply(good, retry, prompt=prompt), good)
        self.assertEqual(calls["n"], 0)
        bad = '```json\n{"title":"Fruit","data":[{"type":"bar","points":[1,2]}]}\n```'
        self.assertEqual(repair_chart_reply(bad, retry, prompt=prompt), good)
        self.assertEqual(calls["n"], 1)

        def still_bad():
            calls["n"] += 1
            return '```chart\n{"data":[{"type":"scatter","x":[1]}]}\n```'

        sentence = repair_chart_reply(bad, still_bad, prompt=prompt)
        self.assertEqual(sentence, CHART_FALLBACK)
        self.assertNotIn("```", sentence)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(sentence.count("."), 1)

    def test_salvageable_json_is_a_chart_fence(self):
        prompt = "Plot the fruit stand"
        body = '{"title":"Fruit","data":[{"type":"bar","y":[1, 2]}]}'
        fence = "```chart\n" + body + "\n```"
        calls = {"n": 0}

        def retry():
            calls["n"] += 1
            return "unused"

        fenced = "Here.\n```json\n" + body + "\n```\n"
        self.assertEqual(repair_chart_reply(fenced, retry, prompt=prompt), fence)
        self.assertEqual(
            repair_chart_reply("```JSON\n" + body + "\n```", retry, prompt=prompt),
            fence,
        )
        self.assertEqual(repair_chart_reply(body, retry, prompt=prompt), fence)
        self.assertEqual(
            repair_chart_reply("```json\r\n" + body + "\r\n```", retry, prompt=prompt),
            fence,
        )
        self.assertEqual(calls["n"], 0)
        self.assertTrue(chart_json_ok(body))

        pie = '{"data":[{"type":"pie","values":[1,2]}]}'
        self.assertEqual(
            repair_chart_reply("```json\n" + pie + "\n```", retry, prompt=prompt),
            "```chart\n" + pie + "\n```",
        )
        plotly = "```plotly\n" + body + "\n```"
        self.assertEqual(repair_chart_reply(plotly, retry, prompt=prompt), plotly)
        chart = "Apples lead.\n" + fence
        self.assertEqual(repair_chart_reply(chart, retry, prompt=prompt), chart)
        mixed = fence + "\n```json\n" + body + "\n```"
        self.assertEqual(
            repair_chart_reply(mixed, retry, prompt=prompt), fence + "\n" + fence
        )
        self.assertNotIn(
            "```json", repair_chart_reply(mixed, retry, prompt=prompt).lower()
        )
        python = '```python\n{"data":[{"type":"bar","y":[1]}]}\n```'
        self.assertEqual(repair_chart_reply(python, retry, prompt=prompt), python)
        self.assertEqual(
            repair_chart_reply('{"host":"pi4"}', retry, prompt=prompt), '{"host":"pi4"}'
        )
        self.assertEqual(
            repair_chart_reply("No points were given.", retry, prompt=prompt),
            "No points were given.",
        )
        kept = repair_chart_reply(fenced, retry, prompt="what host is this")
        self.assertEqual(kept, fenced)
        self.assertIn("```json", kept)
        self.assertEqual(calls["n"], 0)

        bad = '```json\n{"title":"Fruit","data":[{"type":"bar","points":[1,2]}]}\n```'
        loose = '```JSON\n{"title":"Picnic","data":[{"type":"Bar","y":[2, 4,],}]}\n```'

        def salvage():
            calls["n"] += 1
            return "```json\n" + body + "\n```"

        self.assertEqual(repair_chart_reply(bad, salvage, prompt=prompt), fence)
        self.assertEqual(calls["n"], 1)

        def bare():
            calls["n"] += 1
            return "  " + body + "  "

        self.assertEqual(repair_chart_reply(loose, bare, prompt=prompt), fence)
        self.assertEqual(calls["n"], 2)

        def prose():
            calls["n"] += 1
            return "I drew it in my head."

        sentence = repair_chart_reply(bad, prose, prompt=prompt)
        self.assertEqual(sentence, CHART_FALLBACK)
        self.assertNotIn("```json", sentence)
        self.assertEqual(calls["n"], 3)


class Documents(unittest.TestCase):
    def test_excerpt_follows_the_question(self):
        filler = "padding " * 400
        body = filler + " The catalyst heats the chamber to 400 degrees. " + filler
        fitted = fit_document(body, "What temperature does the catalyst reach?")
        self.assertLessEqual(len(fitted), DOC_FIT_CHARS)
        self.assertIn("400 degrees", fitted)
        self.assertNotIn(body, fitted)
        outbound = fit_outbound(
            [
                {
                    "role": "user",
                    "content": "What temperature does the catalyst reach?\n\n---\n"
                    + body,
                }
            ]
        )
        self.assertEqual(outbound[0]["role"], "system")
        self.assertIn("400 degrees", outbound[1]["content"])
        self.assertNotIn(body, outbound[1]["content"])

    def test_excerpt_budget_follows_num_ctx(self):
        question = "What temperature does the catalyst reach?"
        wide = excerpt_limit(2048, question, 256)
        tight = excerpt_limit(512, question, 256)
        self.assertEqual(DOC_FIT_CHARS, excerpt_limit(2048))
        self.assertLessEqual(wide, 4096)
        self.assertLess(tight, wide)
        self.assertLess(excerpt_limit(2048, "note " * 900, 768), wide)
        filler = "padding " * 800
        body = filler + " The catalyst heats the chamber to 400 degrees. " + filler
        fitted = fit_document(body, question, wide)
        self.assertLessEqual(len(fitted), wide)
        self.assertIn("400 degrees", fitted)
        small = fit_document(body, question, tight)
        self.assertLessEqual(len(small), tight)
        self.assertLess(len(small), len(fitted))
        self.assertIn("400 degrees", small)
        outbound = fit_outbound(
            [{"role": "user", "content": question + "\n\n---\n" + body}],
            num_ctx=512,
            reply_tokens=256,
        )
        sent = outbound[1]["content"]
        _question, excerpt = sent.split("\n---\n", 1)
        self.assertLessEqual(len(excerpt.strip()), tight)
        self.assertIn("400 degrees", excerpt)
        self.assertNotIn(body, sent)


class Preload(unittest.TestCase):
    def test_pro_payload_keeps_the_model_resident(self):
        payload = pro_preload_payload()
        self.assertEqual(payload["model"], "qwen3:1.7b")
        self.assertEqual(payload["keep_alive"], -1)
        self.assertNotEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["options"]["num_predict"], 1)
        self.assertEqual(payload["options"]["num_ctx"], 2048)
        self.assertEqual(payload["options"]["num_batch"], 64)
        self.assertEqual(payload["options"]["num_thread"], 4)
        flash = pro_preload_payload("qwen3:0.6b")
        self.assertEqual(flash["options"]["num_ctx"], 1536)
        self.assertEqual(flash["options"]["num_batch"], 128)
        self.assertEqual(flash["options"]["num_thread"], 4)
        self.assertGreaterEqual(PRELOAD_TIMEOUT_S, 180)
        self.assertFalse(payload["think"])
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"keep_alive":-1', script)
        self.assertIn('"think": False', script)
        self.assertIn('ollama show "$OLLAMA_PRO_MODEL"', script)
        executed = [
            line.strip()
            for line in script.splitlines()
            if "ollama pull" in line and not line.strip().startswith("echo")
        ]
        self.assertTrue(all("1.5b" not in line for line in executed))

    def test_an_evicted_tag_is_warmed_again(self):
        seen: list[dict] = []

        class _Resp:
            def read(self) -> bytes:
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout):
            del timeout
            seen.append(json.loads(request.data.decode()))
            return _Resp()

        env = {"PI_PAIR_ROLE": "brain"}
        with patch.dict(os.environ, env, clear=False):
            with patch("pair.preload.resident_models", return_value=[]):
                with patch("pair.preload.open_json_request", opener):
                    rewarm_pro_if_evicted()
        models = [item["model"] for item in seen]
        self.assertEqual(models, ["qwen3:0.6b", "qwen3:1.7b"])
        self.assertTrue(all(item["keep_alive"] == -1 for item in seen))
        seen.clear()
        with patch.dict(os.environ, env, clear=False):
            with patch("pair.preload.resident_models", return_value=["qwen3:0.6b"]):
                with patch("pair.preload.open_json_request", opener):
                    rewarm_pro_if_evicted()
        self.assertEqual([item["model"] for item in seen], ["qwen3:1.7b"])
        self.assertEqual(seen[0]["keep_alive"], -1)


class WebPolish(unittest.TestCase):
    def test_streaming_math_diagrams_and_presence(self):
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(ROOT / "web" / "polish.test.mjs"),
            ],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode, 0, completed.stdout + "\n" + completed.stderr
        )
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
