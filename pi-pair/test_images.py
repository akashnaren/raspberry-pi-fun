"""Image cards for a visual reply. No live network."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.images import lookup_images, sanitize_card, search_phrase  # noqa: E402

POSTER = (
    "https://upload.wikimedia.org/wikipedia/en/2/2e/"
    "Inception_%282010%29_theatrical_poster.jpg"
)
PAGE = "https://en.wikipedia.org/wiki/Inception"


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self, n=-1):
        if n is None or n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data

    def close(self):
        return None


def _summary(title, description, image, page, width=220, height=326):
    return json.dumps(
        {
            "type": "standard",
            "title": title,
            "description": description,
            "thumbnail": {"source": image, "width": width, "height": height},
            "content_urls": {"desktop": {"page": page}},
        }
    )


class ImageCards(unittest.TestCase):
    def test_card_markup_keeps_public_images_only(self):
        completed = subprocess.run(
            ["node", "--experimental-strip-types", str(ROOT / "web" / "images.test.mjs")],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + "\n" + completed.stderr)
        self.assertIn("ok", completed.stdout)

    def test_schema_doc_names_the_reply_field(self):
        text = (ROOT / "docs" / "IMAGES.md").read_text(encoding="utf-8")
        for name in ("pi_images", "url", "alt", "title", "caption", "source", "width", "height"):
            self.assertIn(f"`{name}`", text)
        self.assertIn("upload.wikimedia.org", text)
        self.assertIn("failed lookup", text)

    def test_phrase_for_a_movie_a_picture_and_a_plain_question(self):
        self.assertEqual(search_phrase("Tell me about the movie Inception"), "Inception film")
        self.assertEqual(search_phrase("poster for Parasite"), "Parasite film")
        self.assertEqual(search_phrase("picture of a red panda"), "red panda")
        self.assertEqual(search_phrase("what does the Eiffel Tower look like?"), "Eiffel Tower")
        self.assertEqual(search_phrase("good science fiction movies"), "good science fiction films")
        self.assertEqual(search_phrase("how tall is the bench"), "")
        self.assertEqual(search_phrase("movie"), "")
        self.assertEqual(search_phrase(""), "")

    def test_movie_skips_a_soundtrack_and_a_private_image(self):
        fetched = []

        def opener(request, timeout=None):
            url = request.full_url
            fetched.append(url)
            if "list=search" in url:
                self.assertIn("Inception+film", url)
                return _Resp(
                    json.dumps(
                        {
                            "query": {
                                "search": [
                                    {"title": "Inception (soundtrack)"},
                                    {"title": "Inception"},
                                    {"title": "Local"},
                                ]
                            }
                        }
                    )
                )
            if "Inception_%28soundtrack%29" in url:
                return _Resp(
                    _summary(
                        "Inception (soundtrack)",
                        "2010 soundtrack album by Hans Zimmer",
                        "https://upload.wikimedia.org/wikipedia/en/album.jpg",
                        "https://en.wikipedia.org/wiki/Inception_(soundtrack)",
                    )
                )
            if url.endswith("/Inception"):
                return _Resp(
                    _summary(
                        "Inception",
                        "2010 film by Christopher Nolan",
                        POSTER + "?utm_source=en.wikipedia.org",
                        PAGE,
                    )
                )
            if url.endswith("/Local"):
                return _Resp(
                    _summary(
                        "Local",
                        "2010 film",
                        "http://127.0.0.1/secret.jpg",
                        "https://en.wikipedia.org/wiki/Local",
                    )
                )
            raise AssertionError(url)

        cards = lookup_images("Tell me about the movie Inception", opener=opener)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], POSTER + "?utm_source=en.wikipedia.org")
        self.assertEqual(cards[0]["title"], "Inception")
        self.assertEqual(cards[0]["caption"], "2010 film by Christopher Nolan")
        self.assertEqual(cards[0]["source"], PAGE)
        self.assertEqual(cards[0]["alt"], "Inception. 2010 film by Christopher Nolan")
        self.assertEqual(cards[0]["width"], 220)
        self.assertEqual(cards[0]["height"], 326)
        self.assertTrue(all("127.0.0.1" not in url for url in fetched))
        self.assertNotIn("album", json.dumps(cards))

    def test_several_films_cap_at_three(self):
        titles = ["One (film)", "Two (film)", "Three (film)", "Four (film)"]

        def opener(request, timeout=None):
            url = request.full_url
            if "list=search" in url:
                return _Resp(json.dumps({"query": {"search": [{"title": title} for title in titles]}}))
            for title in titles:
                slug = quote(title.replace(" ", "_"), safe="")
                if url.endswith("/" + slug):
                    return _Resp(
                        _summary(
                            title,
                            "film",
                            f"https://upload.wikimedia.org/wikipedia/en/{slug}.jpg",
                            f"https://en.wikipedia.org/wiki/{slug}",
                        )
                    )
            raise AssertionError(url)

        cards = lookup_images("good science fiction movies", opener=opener)
        self.assertEqual([card["title"] for card in cards], titles[:3])

    def test_lookup_failure_is_an_empty_list(self):
        def opener(request, timeout=None):
            raise OSError("wiki down")

        self.assertEqual(lookup_images("poster for Parasite", opener=opener), [])

        def empty(request, timeout=None):
            if "list=search" in request.full_url:
                return _Resp(json.dumps({"query": {"search": [{"title": "Inception"}]}}))
            return _Resp("not-json")

        self.assertEqual(lookup_images("the movie Inception", opener=empty), [])

    def test_plain_question_does_not_fetch(self):
        def opener(request, timeout=None):
            raise AssertionError(request.full_url)

        self.assertEqual(lookup_images("how tall is the bench", opener=opener), [])
        self.assertEqual(lookup_images("", opener=opener), [])

    def test_redirect_off_wikipedia_is_not_followed(self):
        fetched = []

        def opener(request, timeout=None):
            fetched.append(request.full_url)
            if "list=search" in request.full_url:
                return _Resp(json.dumps({"query": {"search": [{"title": "Inception"}]}}))
            return _Resp("", status=302, headers={"Location": "https://evil.example/steal"})

        self.assertEqual(lookup_images("movie Inception", opener=opener), [])
        self.assertEqual(len(fetched), 2)
        self.assertTrue(all("evil.example" not in url for url in fetched))

    def test_sanitize_drops_non_public_images(self):
        good = sanitize_card(
            {
                "url": "//thumb.wikimedia.org/wikipedia/commons/thumb/a/a.png",
                "title": "Panda",
                "caption": "mammal",
                "source": "https://en.wikipedia.org/wiki/Red_panda",
                "width": 330,
                "height": 220,
                "html": "<script>",
            }
        )
        self.assertEqual(
            good["url"],
            "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/a.png",
        )
        self.assertNotIn("html", good)
        self.assertIsNone(sanitize_card({"url": "https://evil.example/a.jpg", "title": "x", "alt": "x"}))
        self.assertIsNone(sanitize_card({"url": "https://upload.wikimedia.org/a.svg", "title": "x", "alt": "x"}))
        kept = sanitize_card(
            {
                "url": "https://upload.wikimedia.org/a.jpg",
                "title": "x",
                "alt": "x",
                "source": "http://en.wikipedia.org/wiki/X",
            }
        )
        self.assertEqual(kept["title"], "x")
        self.assertNotIn("source", kept)


if __name__ == "__main__":
    unittest.main()
