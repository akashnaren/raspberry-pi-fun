"""Wikimedia image cards. Structure only. No live network."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import images  # noqa: E402
from pair.images import (  # noqa: E402
    _fetch_json,
    _public_http,
    candidates,
    cards,
    normalize_title,
    sanitize_card,
)

PHOTO = "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour.jpg"
PAGE = "https://en.wikipedia.org/wiki/Eiffel_Tower"
BANNED = ("movie", "film", "car", "poster", "actor", "vehicle", "product")
_BANNED_WORD = re.compile(r"\b(?:" + "|".join(BANNED) + r")\b", re.I)


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.read_limit = None

    def read(self, n=-1):
        self.read_limit = n
        if n is None or n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data

    def close(self):
        return None


def _summary(title, description, image, page, width=320, height=480, kind="standard"):
    return json.dumps(
        {
            "type": kind,
            "title": title,
            "description": description,
            "thumbnail": {"source": image, "width": width, "height": height},
            "content_urls": {"desktop": {"page": page}},
        }
    )


def _photo(title):
    slug = quote(title.replace(" ", "_"), safe="")
    return (
        f"https://upload.wikimedia.org/wikipedia/commons/{slug}.jpg",
        f"https://en.wikipedia.org/wiki/{slug}",
    )


class ImageCards(unittest.TestCase):
    def setUp(self):
        images.reset_image_cache()
        images.JOB_BUDGET_S = 4.0
        os.environ.pop("PI_PAIR_IMAGE_PNG", None)

    def tearDown(self):
        images.reset_image_cache()
        images.JOB_BUDGET_S = 4.0
        os.environ.pop("PI_PAIR_IMAGE_PNG", None)

    def test_card_markup_keeps_public_images_only(self):
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(ROOT / "web" / "images.test.mjs"),
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

    def test_schema_doc_names_the_reply_field(self):
        text = (ROOT / "docs" / "IMAGES.md").read_text(encoding="utf-8")
        for name in (
            "pi_images",
            "url",
            "alt",
            "title",
            "caption",
            "source",
            "width",
            "height",
        ):
            self.assertIn(f"`{name}`", text)
        self.assertIn("upload.wikimedia.org", text)
        self.assertIn("failed lookup", text)
        self.assertIn("never fetched", text)
        self.assertIn("subject-word list", text)
        self.assertIn("No embedding model is loaded", text)

    def test_sanitizer_drops_private_and_non_photos(self):
        good = {
            "url": PHOTO,
            "alt": "Alt text",
            "title": "Eiffel Tower",
            "caption": "A tower",
            "source": PAGE,
            "width": 320,
            "height": 480,
        }
        kept = sanitize_card(good)
        self.assertEqual(kept["url"], PHOTO)
        self.assertEqual(kept["source"], PAGE)
        self.assertEqual(kept["width"], 320)
        blocked = [
            "http://10.1.2.3/a.jpg",
            "http://192.168.0.2/a.jpg",
            "http://172.16.0.1/a.jpg",
            "http://127.0.0.1/a.jpg",
            "http://[::1]/a.jpg",
            "http://169.254.1.1/a.jpg",
            "http://100.64.1.1/a.jpg",
            "http://100.127.1.1/a.jpg",
            "http://0.0.0.0/a.jpg",
            "http://224.0.0.1/a.jpg",
            "https://localhost/a.jpg",
            "https://printer.local/a.jpg",
            "https://box.localhost/a.jpg",
            "https://upload.wikimedia.org:444/a.jpg",
            "https://user:pass@upload.wikimedia.org/a.jpg",
            "javascript:alert(1)",
            "data:image/jpeg;base64,aaaa",
            "https://upload.wikimedia.org/a.svg",
            "https://upload.wikimedia.org/a.svg.png",
            "https://evil.example/a.jpg",
            "https://upload.wikimedia.org/a.gif",
            "https://upload.wikimedia.org/a.png",
            "http://10.0.0.166/api/tags.jpg",
        ]
        for url in blocked:
            self.assertIsNone(sanitize_card(dict(good, url=url)), url)
        self.assertIsNone(sanitize_card(dict(good, width=8001)))
        self.assertIsNone(sanitize_card(dict(good, height=0)))
        self.assertIsNone(sanitize_card(dict(good, width=True)))
        wide = sanitize_card(dict(good, width=8000))
        self.assertEqual(wide["width"], 8000)
        plain = dict(good)
        plain.pop("width")
        plain.pop("height")
        self.assertNotIn("width", sanitize_card(plain))
        http_thumb = sanitize_card(
            dict(
                good,
                url="http://thumb.wikimedia.org/wikipedia/commons/a.jpg",
                source="http://en.wikipedia.org/wiki/Eiffel_Tower",
            )
        )
        self.assertTrue(http_thumb["url"].startswith("http://thumb.wikimedia.org/"))
        self.assertNotIn("source", http_thumb)
        self.assertFalse(_public_http("http://100.64.1.1/a.jpg"))
        self.assertFalse(_public_http("http://127.0.0.1/a.jpg"))
        self.assertTrue(_public_http("http://100.128.1.1/a.jpg"))
        os.environ["PI_PAIR_IMAGE_PNG"] = "1"
        try:
            png = sanitize_card(
                dict(good, url="https://upload.wikimedia.org/wikipedia/commons/a.png")
            )
        finally:
            os.environ.pop("PI_PAIR_IMAGE_PNG", None)
        self.assertTrue(png["url"].endswith(".png"))

    def test_fetch_stays_on_english_wikipedia_and_caps_the_body(self):
        def opener(request, timeout=None):
            raise AssertionError(request.full_url)

        for url in (
            "http://en.wikipedia.org/w/api.php",
            "https://fr.wikipedia.org/w/api.php",
            "https://evil.example/w/api.php",
            "https://user:pass@en.wikipedia.org/w/api.php",
        ):
            with self.assertRaises(ValueError):
                _fetch_json(url, opener)

        called = []

        def redirect(request, timeout=None):
            called.append(request.full_url)
            return _Resp(
                "",
                status=302,
                headers={"Location": "https://evil.example/steal"},
            )

        with self.assertRaises(ValueError):
            _fetch_json("https://en.wikipedia.org/w/api.php?start=1", redirect)
        self.assertEqual(called, ["https://en.wikipedia.org/w/api.php?start=1"])

        hops = {
            "https://en.wikipedia.org/w/api.php?h=0": "https://en.wikipedia.org/w/api.php?h=1",
            "https://en.wikipedia.org/w/api.php?h=1": "https://en.wikipedia.org/w/api.php?h=2",
            "https://en.wikipedia.org/w/api.php?h=2": "https://en.wikipedia.org/w/api.php?h=3",
        }

        def hop(request, timeout=None):
            loc = hops.get(request.full_url)
            self.assertIsNotNone(loc)
            return _Resp("", status=302, headers={"Location": loc})

        with self.assertRaises(ValueError):
            _fetch_json("https://en.wikipedia.org/w/api.php?h=0", hop)

        seen = {}

        def capped(request, timeout=None):
            raw = b'{"ok": true, "pad": "' + (b"a" * 250_000) + b'"}'
            resp = _Resp(raw)
            seen["resp"] = resp
            return resp

        with self.assertRaises(json.JSONDecodeError):
            _fetch_json("https://en.wikipedia.org/w/api.php?big=1", capped)
        self.assertEqual(seen["resp"].read_limit, 200_000)

    def test_candidates_come_from_structure_only(self):
        answer = "\n".join(
            [
                "```plot",
                "1. Hidden Plot",
                "| Apple | 3 |",
                "```",
                "```pdf",
                "1. Hidden Pdf",
                "```",
                "```",
                "1. Hidden Code",
                "```",
                "| Pear | 5 |",
                "1. Eiffel Tower — a note",
                "2) Louvre: museum",
                "- Colosseum (rome)",
                "* Red panda",
                "**British Museum**",
                "See also **Tower Bridge**.",
            ]
        )
        sources = [
            "https://en.wikipedia.org/wiki/Eiffel_Tower",
            {"url": "https://en.wikipedia.org/wiki/Louvre"},
            "http://en.wikipedia.org/wiki/Nope",
            "https://fr.wikipedia.org/wiki/Paris",
            "https://evil.example/wiki/X",
            "http://10.0.0.166:11434/api/tags",
        ]
        found = candidates("1. Question Item should not count", answer, sources)
        titles = [row["title"] for row in found]
        self.assertIn("Eiffel Tower", titles)
        self.assertIn("Louvre", titles)
        self.assertIn("Colosseum", titles)
        self.assertIn("Red panda", titles)
        self.assertIn("British Museum", titles)
        self.assertIn("Tower Bridge", titles)
        self.assertNotIn("Hidden Plot", titles)
        self.assertNotIn("Hidden Pdf", titles)
        self.assertNotIn("Hidden Code", titles)
        self.assertNotIn("Apple", titles)
        self.assertNotIn("Pear", titles)
        self.assertNotIn("Question Item should not count", titles)
        self.assertNotIn("Nope", titles)
        self.assertTrue(all(row["signal"] in (1, 2) for row in found))
        self.assertLessEqual(len(found), 6)
        self.assertEqual(len(found), len({row["norm"] for row in found}))
        again = candidates("other question", answer, sources)
        self.assertEqual(
            [(row["norm"], row["signal"]) for row in found],
            [(row["norm"], row["signal"]) for row in again],
        )

    def test_topic_words_do_not_create_candidates(self):
        prose = "Tell me about the movie Inception"
        answer = "It is a film about a car."
        self.assertEqual(candidates(prose, answer, []), [])
        self.assertEqual(
            candidates(
                "good science fiction movies",
                "movies: Inception, Interstellar, The Dark Knight",
                [],
            ),
            [],
        )
        self.assertEqual(
            candidates(
                "top sports cars",
                "films, posters, actors, vehicles, and products",
                [],
            ),
            [],
        )
        structured_q = "best movie about a car"
        structured_a = "\n".join(
            [
                "The movie shows a car.",
                "1. Inception",
                "2. Eiffel Tower",
                "- Red panda",
                "**Louvre**",
            ]
        )
        source = ["https://en.wikipedia.org/wiki/Colosseum"]

        def swap(text):
            return (
                text.replace("movie", "blick")
                .replace("car", "zorb")
                .replace("film", "quox")
            )

        before = candidates(structured_q, structured_a, source)
        after = candidates(swap(structured_q), swap(structured_a), source)
        self.assertEqual(before, after)
        self.assertGreaterEqual(len(before), 4)
        for name in (
            "search_phrase",
            "visual_mode",
            "suits_visuals",
            "_MOVIE",
            "_VISUAL",
            "_LEAD",
            "_VISUAL_NOUN",
            "_mentions_film",
        ):
            self.assertFalse(hasattr(images, name), name)

    def test_source_file_has_no_topic_word_lists(self):
        path = ROOT / "pair" / "images.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = _BANNED_WORD.search(node.value)
                self.assertIsNone(match, node.value)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], {"queue", "canned"})
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                self.assertNotIn(module.split(".")[-1], {"queue", "canned", "memory"})

    def test_gate_keeps_a_standard_photo_with_a_title_match(self):
        self.assertEqual(normalize_title("Inception (film)"), "inception")

        calls = []

        def opener(request, timeout=None):
            url = request.full_url
            calls.append(url)
            if url.endswith("/Inception"):
                return _Resp(
                    _summary(
                        "Inception (film)",
                        "2010 story",
                        "https://upload.wikimedia.org/wikipedia/commons/inception.jpg",
                        "https://en.wikipedia.org/wiki/Inception_(film)",
                    )
                )
            if url.endswith("/Eiffel_Tower"):
                return _Resp(_summary("Eiffel", "tower", PHOTO, PAGE))
            if url.endswith("/Tower"):
                return _Resp(_summary("Eiffel Tower", "tower", PHOTO, PAGE))
            if url.endswith("/Ambiguous"):
                return _Resp(
                    _summary("Ambiguous", "page", PHOTO, PAGE, kind="disambiguation")
                )
            if url.endswith("/Diagram"):
                return _Resp(
                    _summary(
                        "Diagram",
                        "drawing",
                        "https://upload.wikimedia.org/wikipedia/commons/a.svg",
                        PAGE,
                    )
                )
            if url.endswith("/Png_Knob"):
                return _Resp(
                    _summary(
                        "Png Knob",
                        "raster",
                        "https://upload.wikimedia.org/wikipedia/commons/a.png",
                        "https://en.wikipedia.org/wiki/Png_Knob",
                    )
                )
            raise AssertionError(url)

        listed = cards(
            {
                "question": "names",
                "answer": "1. Inception\n2. Ambiguous\n- Diagram",
                "sources": [
                    "http://10.0.0.166:11434/api/tags",
                    "https://evil.example/wiki/X",
                    "https://en.wikipedia.org/wiki/Eiffel_Tower",
                ],
            },
            opener=opener,
        )
        self.assertTrue(calls)
        self.assertTrue(
            all(urlparse(url).hostname == "en.wikipedia.org" for url in calls)
        )
        self.assertFalse(any("list=search" in url for url in calls))
        self.assertFalse(
            any("10.0.0.166" in url or "evil.example" in url for url in calls)
        )
        titles = [card["title"] for card in listed["cards"]]
        self.assertIn("Inception (film)", titles)
        self.assertIn("Eiffel", titles)
        self.assertNotIn("Ambiguous", titles)
        self.assertNotIn("Diagram", titles)
        self.assertLessEqual(len(listed["cards"]), 4)

        before = len(calls)
        loose = cards(
            {"question": "q", "answer": "**Tower**", "sources": []},
            opener=opener,
        )
        self.assertEqual(loose["cards"], [])
        self.assertGreater(len(calls), before)

        images.reset_image_cache()
        dropped = cards(
            {"question": "q", "answer": "1. Png Knob", "sources": []},
            opener=opener,
        )
        self.assertEqual(dropped["cards"], [])
        images.reset_image_cache()
        os.environ["PI_PAIR_IMAGE_PNG"] = "1"
        try:
            kept = cards(
                {"question": "q", "answer": "1. Png Knob", "sources": []},
                opener=opener,
            )
        finally:
            os.environ.pop("PI_PAIR_IMAGE_PNG", None)
        self.assertEqual([card["title"] for card in kept["cards"]], ["Png Knob"])

    def test_question_search_runs_only_when_structure_is_empty(self):
        calls = []

        def opener(request, timeout=None):
            url = request.full_url
            calls.append(url)
            if "list=search" in url:
                return _Resp(
                    json.dumps(
                        {
                            "query": {
                                "search": [
                                    {"title": "Inception (disambiguation)"},
                                    {"title": "Inception"},
                                    {"title": "Unrelated Page"},
                                ]
                            }
                        }
                    )
                )
            if url.endswith("/Inception"):
                return _Resp(
                    _summary(
                        "Inception",
                        "story",
                        PHOTO,
                        "https://en.wikipedia.org/wiki/Inception",
                    )
                )
            return _Resp("{}")

        question = "Tell me about the movie Inception"
        found = cards(
            {"question": question, "answer": "It is a story.", "sources": []},
            opener=opener,
        )
        self.assertEqual([card["title"] for card in found["cards"]], ["Inception"])
        search = next(url for url in calls if "list=search" in url)
        self.assertEqual(urlparse(search).hostname, "en.wikipedia.org")
        self.assertIn("movie", search)
        self.assertNotIn("Inception+film", search)
        self.assertNotIn("Inception%20film", search)
        calls.clear()
        again = cards(
            {"question": question, "answer": "It is a story.", "sources": []},
            opener=opener,
        )
        self.assertEqual(again["cards"], found["cards"])
        self.assertEqual(calls, [])

        images.reset_image_cache()
        missed = []

        def missing(request, timeout=None):
            url = request.full_url
            missed.append(url)
            if "list=search" in url:
                return _Resp(json.dumps({"query": {"search": [{"title": "Paris"}]}}))
            if url.endswith("/Paris"):
                return _Resp(_summary("Paris", "city", PHOTO, PAGE))
            return _Resp("{}")

        empty = cards(
            {
                "question": "what does the tower look like",
                "answer": "It is tall.",
                "sources": [],
            },
            opener=missing,
        )
        self.assertEqual(empty["cards"], [])
        self.assertTrue(any("list=search" in url for url in missed))

    def test_at_most_four_cards_and_seven_fetches(self):
        calls = []

        def opener(request, timeout=None):
            url = request.full_url
            calls.append(url)
            slug = unquote(url.rstrip("/").rsplit("/", 1)[-1])
            title = slug.replace("_", " ")
            image, page = _photo(title)
            return _Resp(_summary(title, "place", image, page))

        answer = "\n".join(f"{index}. Place {index}" for index in range(1, 9))
        found = candidates("places", answer, [])
        self.assertEqual(len(found), 6)
        result = cards({"question": "places", "answer": answer}, opener=opener)
        self.assertEqual(len(result["cards"]), 4)
        self.assertEqual(len(calls), 6)
        self.assertTrue(all("list=search" not in url for url in calls))
        self.assertTrue(
            all(urlparse(url).hostname == "en.wikipedia.org" for url in calls)
        )

    def test_deadline_returns_without_waiting_out_a_sleeper(self):
        images.JOB_BUDGET_S = 0.35

        def opener(request, timeout=None):
            time.sleep(2)
            return _Resp("{}")

        started = time.monotonic()
        result = cards(
            {"question": "q", "answer": "1. Deadline Sleeper Zzz"},
            opener=opener,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result["cards"], [])
        self.assertLess(elapsed, 1.0)
        self.assertEqual(images.JOB_BUDGET_S, 0.35)

    def test_semaphore_overflow_returns_empty(self):
        self.assertTrue(images._JOBS.acquire(timeout=1))
        self.assertTrue(images._JOBS.acquire(timeout=1))
        try:
            started = time.monotonic()
            result = cards({"question": "q", "answer": "1. Overflow Place"})
            elapsed = time.monotonic() - started
        finally:
            images._JOBS.release()
            images._JOBS.release()
        self.assertEqual(result, {"ok": True, "cards": []})
        self.assertLess(elapsed, 0.2)

    def test_cache_hit_skips_http(self):
        calls = []

        def opener(request, timeout=None):
            calls.append(request.full_url)
            return _Resp(_summary("Eiffel Tower", "tower", PHOTO, PAGE))

        payload = {"question": "q", "answer": "1. Eiffel Tower", "sources": []}
        first = cards(payload, opener=opener)
        self.assertEqual(len(first["cards"]), 1)
        self.assertEqual(len(calls), 1)
        second = cards(payload, opener=opener)
        self.assertEqual(second["cards"], first["cards"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
