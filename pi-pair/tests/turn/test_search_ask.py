"""The Search button, and the short explicit-intent backup."""

from __future__ import annotations

import unittest

from pair.turn.search_ask import (
    CUES,
    explicit_search,
    web_search_request,
    who_note_text,
)
from pair.turn.shape import ATTACH_MARK, shape_messages

_MATCH = (
    "Who is Natan Weitzman from Tesla - use your tools web search",
    "What is the latest news?",
    "what is the latest news on NASA",
    "latest news about Tesla",
    "news about X",
    "news on the launch",
    "latest on NASA",
    "what's the latest",
    "what's the latest on NASA",
    "what's the latest about Tesla",
    "What is the latest?",
    "latest Tesla news",
    "search the web for X",
    "search the web for the museum hours",
    "web search the score",
    "please search for the fire chief",
    "look up X",
    "look up the mayor of Lyon",
    "look it up",
    "lookup the recall",
    "use the web to check the hours",
    "use tools to find the speech",
    "google X",
    "google the exchange rate",
    "what is in the news",
)
_SKIP = (
    "hi",
    "Hello, how are you?",
    "Good morning.",
    "What is 2+2?",
    "Write a Python function that reverses a string.",
    "Write a four-line poem about rain.",
    "Write a poem about today.",
    "Write a Python function that returns today's date.",
    "What is the current time zone of Tokyo?",
    "Explain electric current in simple terms.",
    "Summarize the news in one sentence: the cat sat on the mat.",
    "How do I binary search a sorted list?",
    "NASA latest",
    "latest horror movies",
    "the current price of rice",
    "what happened today",
    "where is the current long bench",
    "What is the latest height of the bench in the hall?",
    "how to follow people on X",
    "bath bombs recipe",
    "Stalker 1979 film",
    "track my package",
    "top 5 horror movies",
    "List every prime number from 1 to 50.",
    "What are the top 10 cities to visit in Europe?",
    "Explain what a binary search tree is, with a short example.",
    "https://news.example.com/story",
    "See https://www.google.com/search?q=tesla",
)


class SearchAsk(unittest.TestCase):
    def test_web_search_accepts_only_booleans(self):
        forced = {"web_search": True, "messages": []}
        self.assertTrue(web_search_request(forced))
        self.assertNotIn("web_search", forced)
        self.assertFalse(web_search_request({"messages": []}))
        self.assertFalse(web_search_request({"web_search": False}))
        for value in (1, "true", "yes", None):
            with self.assertRaises(ValueError) as caught:
                web_search_request({"web_search": value})
            self.assertEqual(str(caught.exception), "web_search must be true or false.")

    def test_explicit_asks_match_and_ordinary_lines_do_not(self):
        self.assertGreaterEqual(len(CUES), 8)
        self.assertLessEqual(len(CUES), 16)
        for prompt in _MATCH:
            self.assertTrue(explicit_search(prompt), prompt)
        for prompt in _SKIP:
            self.assertFalse(explicit_search(prompt), prompt)
        attached = f"what does this say{ATTACH_MARK}{'A' * 90}"
        self.assertFalse(explicit_search(attached))
        asked = f"look up this note{ATTACH_MARK}{'A' * 90}"
        self.assertTrue(explicit_search(asked))

    def test_who_is_name_searches_unless_it_is_not_a_person(self):
        self.assertTrue(explicit_search("who is Sid Nair"))
        self.assertFalse(explicit_search("who is my mom"))
        self.assertFalse(explicit_search("who is in the meeting"))
        prior = "Priya is my sister and she lives in Austin."
        self.assertFalse(explicit_search("who is Priya", prior))
        self.assertTrue(explicit_search("who is Priya"))

    def test_who_is_note(self):
        hits = [
            "- Sid Nair - Engineering Manager (https://www.linkedin.com/in/sid): Engineering Manager.",
            "- Siddhartha N. at Etched (https://www.etched.com/s): Member of Technical Staff at Etched.",
            "- Sam page (https://example.com/a): a short bio.",
            "- Fourth hit (https://example.com/z): unrelated person elsewhere.",
        ]
        full = "Web search notes.\n" + "\n".join(hits)
        notes = (
            (
                "who is Sid Nair",
                ("Result 1 (linkedin.com):", "Result 2 (etched.com):"),
                "",
            ),
            (
                "who is Sam Altman's husband",
                ("Result 1 (linkedin.com):",),
                "No result mentions Sam Altman.",
            ),
            (
                "who is Dr. Sid Nair",
                ("Result 1 (linkedin.com):",),
                "No result mentions Dr Sid.",
            ),
        )
        for prompt, labels, gap in notes:
            note = who_note_text(prompt, full)
            self.assertTrue(note.startswith("Web search:"), prompt)
            self.assertNotIn("[1]", note, prompt)
            self.assertNotIn("unrelated", note, prompt)
            self.assertNotIn("Altman's", note, prompt)
            self.assertNotIn("No result mentions Dr.", note, prompt)
            for line in note.splitlines():
                self.assertFalse(line.startswith("- "), prompt)
            for label in labels:
                self.assertIn(label, note, prompt)
            if len(labels) > 1:
                self.assertLess(note.index(labels[0]), note.index(labels[1]), prompt)
            if gap:
                self.assertIn(gap, note, prompt)
                self.assertLess(note.index(gap), note.index(labels[0]), prompt)
            else:
                self.assertNotIn("No result mentions", note, prompt)
        sid = who_note_text("who is Sid Nair", full)
        rows = shape_messages(
            [{"role": "user", "content": "who is Sid Nair"}],
            "who is Sid Nair",
            notes=sid,
        )
        folded = rows[-1]["content"]
        self.assertLess(
            folded.index("Result 1 (linkedin.com):"),
            folded.index("Result 2 (etched.com):"),
        )
        skips = (
            ("who is Will Smith", "I will call you later.", True),
            ("who is Sam Iyer", "who is Sam Altman\nSam Altman runs OpenAI.", True),
            ("who is Sid Nair at Etched", "who is Sid Nair", True),
            ("who is Priya", "Priya is my sister and she lives in Austin.", False),
        )
        for prompt, prior, want in skips:
            self.assertEqual(explicit_search(prompt, prior), want, prompt)
