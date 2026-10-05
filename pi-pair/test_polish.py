"""Charts, lists, document excerpts, and the Pro preload."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.charts import (  # noqa: E402
    CHART_FALLBACK,
    chart_json_ok,
    is_structured_request,
    normalize_chart_reply,
    parabola_chart,
    repair_chart_reply,
    structure_hint,
)
from pair.docfit import DOC_FIT_CHARS, excerpt_limit, fit_document, fit_outbound  # noqa: E402
from pair.images import cards_for_answer, item_names, visual_mode  # noqa: E402
from pair.lists import finish_numbered, list_budget, list_complete, list_count, merge_list  # noqa: E402
from pair.preload import pro_preload_payload  # noqa: E402


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

        self.assertTrue(chart_json_ok('{"title":"Fruit","data":[{"type":"pie","values":[1,2]}]}'))
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

    def test_a_loose_chart_fence_becomes_one_block(self):
        messy = 'Here.\n```JSON\n{"title":"  Picnic   foods","data":[{"type":"Bar","y":[2, 4,],}],}\n```\n'
        clean = normalize_chart_reply(messy)
        self.assertEqual(clean.count("```"), 2)
        self.assertTrue(clean.startswith("Here.\n```chart\n"))
        spec = json.loads(clean.split("```chart\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(spec["data"][0]["type"], "bar")
        self.assertEqual(spec["data"][0]["y"], [2, 4])
        self.assertEqual(spec["title"], "Picnic foods")
        self.assertTrue(chart_json_ok(clean.split("```chart\n", 1)[1].split("\n```", 1)[0]))
        self.assertEqual(normalize_chart_reply(clean), clean)
        kept = '```chart\n{"title":"Fruit","data":[{"type":"bar","y":[1,2]}]}\n```'
        self.assertEqual(normalize_chart_reply(kept), kept)
        pie = '```plotly\n{"data":[{"type":"pie","values":[1.0, 2.5]}]}\n```'
        drawn = normalize_chart_reply(pie)
        pie_spec = json.loads(drawn.split("```chart\n", 1)[1].split("\n```", 1)[0])
        self.assertEqual(pie_spec["data"][0], {"type": "pie", "y": [1, 2.5]})
        rounded = normalize_chart_reply('```JSON\n{"data":[{"type":"line","y":[1.23456,],}]}\n```')
        self.assertIn('"y":[1.2346]', rounded)
        bare = '{"data":[{"type":"scatter","mode":"lines","name":"Load","y":[1,2]}]}'
        wrapped = normalize_chart_reply(bare)
        self.assertTrue(wrapped.startswith("```chart\n"))
        self.assertEqual(normalize_chart_reply("hello"), "hello")
        self.assertEqual(normalize_chart_reply('{"host":"pi4"}'), '{"host":"pi4"}')
        mermaid = "```mermaid\nflowchart TD\nA-->B\n```"
        self.assertEqual(normalize_chart_reply(mermaid), mermaid)
        python = '```python\n{"data":[{"type":"bar","y":[1]}]}\n```'
        self.assertEqual(normalize_chart_reply(python), python)
        hostile = '```chart\n{"data":[{"type":"bar","x":["<b>"],"y":[1]}]}\n```'
        self.assertEqual(normalize_chart_reply(hostile), hostile)
        missed = '```chart\n{"data":[{"type":"bar","points":[1,2]}]}\n```'
        self.assertEqual(normalize_chart_reply(missed), missed)

    def test_normalize_runs_before_repair_and_leaves_the_retry(self):
        prompt = "plot a bar chart of picnic foods"
        loose = 'Here.\n```JSON\n{"title":"Picnic","data":[{"type":"Bar","y":[2, 4,],}],}\n```\n'
        calls = {"n": 0}

        def retry():
            calls["n"] += 1
            return "still bad"

        clean = normalize_chart_reply(loose)
        self.assertEqual(repair_chart_reply(clean, retry, prompt=prompt), clean)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(repair_chart_reply(loose, retry, prompt=prompt), CHART_FALLBACK)
        self.assertEqual(calls["n"], 1)
        missed = '```chart\n{"data":[{"type":"bar","points":[1]}]}\n```'
        salvage = '```json\n{"data":[{"type":"Line","y":[3, 4,],}]}\n```'

        def again():
            calls["n"] += 1
            return normalize_chart_reply(salvage)

        kept = repair_chart_reply(normalize_chart_reply(missed), again, prompt=prompt)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(kept, normalize_chart_reply(salvage))
        self.assertNotEqual(kept, CHART_FALLBACK)
        config = '```json\n{"host":"pi4"}\n```'
        self.assertEqual(normalize_chart_reply(config), config)
        self.assertEqual(repair_chart_reply(config, retry, prompt="what host is this"), config)
        self.assertEqual(calls["n"], 2)


class Lists(unittest.TestCase):
    def test_a_short_list_is_continued_until_n(self):
        prompt = "top 10 movies"
        self.assertEqual(list_count(prompt), 10)
        self.assertEqual(list_budget(prompt, 256), 448)
        partial = "\n".join(f"{i}. Title {i}" for i in range(1, 8))
        self.assertFalse(list_complete(partial, 10))

        def more(text, count):
            self.assertEqual(count, 10)
            start = list_count_from(text) + 1
            return "\n".join(f"{i}. Title {i}" for i in range(start, count + 1))

        done = finish_numbered(prompt, partial, more)
        self.assertTrue(list_complete(done, 10))
        self.assertIn("7. Title 7", done)
        self.assertIn("10. Title 10", done)
        self.assertEqual(done.count("1. Title 1"), 1)

    def test_a_finished_list_is_left_alone(self):
        calls = []
        full = "\n".join(f"{i}. Item {i}" for i in range(1, 6))
        done = finish_numbered("list 5 books", full, lambda *_args: calls.append(1))
        self.assertEqual(done, full)
        self.assertEqual(calls, [])
        self.assertEqual(merge_list(full, "3. Item 3\n6. Extra", 5), full + "\n6. Extra")


def list_count_from(text: str) -> int:
    nums = [int(line.split(".", 1)[0]) for line in text.splitlines() if line[:1].isdigit()]
    return nums[-1] if nums else 0


class Documents(unittest.TestCase):
    def test_excerpt_follows_the_question(self):
        filler = "padding " * 400
        body = filler + " The catalyst heats the chamber to 400 degrees. " + filler
        fitted = fit_document(body, "What temperature does the catalyst reach?")
        self.assertLessEqual(len(fitted), DOC_FIT_CHARS)
        self.assertIn("400 degrees", fitted)
        self.assertNotIn(body, fitted)
        outbound = fit_outbound(
            [{"role": "user", "content": "What temperature does the catalyst reach?\n\n---\n" + body}]
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
        self.assertEqual(payload["model"], "qwen2.5:1.5b")
        self.assertEqual(payload["keep_alive"], -1)
        self.assertNotEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["options"]["num_predict"], 1)
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"keep_alive":-1', script)
        self.assertIn("ollama show qwen2.5:1.5b", script)
        executed = [
            line.strip()
            for line in script.splitlines()
            if "ollama pull" in line and not line.strip().startswith("echo")
        ]
        self.assertTrue(all("1.5b" not in line for line in executed))


class VisualLists(unittest.TestCase):
    def test_each_item_gets_a_card_and_math_does_not(self):
        self.assertEqual(visual_mode("top 5 cars"), "each")
        self.assertEqual(visual_mode("Tell me about the movie Inception"), "one")
        self.assertEqual(visual_mode("what is the derivative of x squared"), "none")
        self.assertEqual(visual_mode("top 10 prime numbers"), "none")
        answer = "1. Dune — desert\n2. Arrival — language"
        self.assertEqual(item_names(answer, 5), ["Dune", "Arrival"])

        def opener(request, timeout=None):
            url = request.full_url
            title = "Dune" if "Dune" in url else "Arrival"
            if "list=search" in url:
                return _Resp(json.dumps({"query": {"search": [{"title": title + " (film)"}]}}))
            return _Resp(
                json.dumps(
                    {
                        "type": "standard",
                        "title": title,
                        "description": "film",
                        "thumbnail": {
                            "source": f"https://upload.wikimedia.org/wikipedia/en/{title}.jpg",
                            "width": 100,
                            "height": 140,
                        },
                        "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{title}"}},
                    }
                )
            )

        cards = cards_for_answer("top 2 movies", answer, opener=opener)
        self.assertEqual([card["title"] for card in cards], ["Dune", "Arrival"])
        self.assertEqual(cards_for_answer("top 10 prime numbers", "1. 2\n2. 3"), [])


class _Resp:
    def __init__(self, body: str):
        self._body = body.encode()

    def read(self, _n=-1):
        data, self._body = self._body, b""
        return data

    def close(self):
        return None


class WebPolish(unittest.TestCase):
    def test_streaming_math_diagrams_and_presence(self):
        completed = subprocess.run(
            ["node", "--experimental-strip-types", str(ROOT / "web" / "polish.test.mjs")],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + "\n" + completed.stderr)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
