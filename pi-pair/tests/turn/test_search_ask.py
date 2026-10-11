"""The Search button, and the short explicit-intent backup."""

from __future__ import annotations

import unittest

from pair.turn.search_ask import CUES, explicit_search, web_search_request
from pair.turn.shape import ATTACH_MARK

_MATCH = (
    "Who is Natan Weitzman from Tesla - use your tools web search",
    "What is the latest news?",
    "NASA latest",
    "search the web for the museum hours",
    "web search the score",
    "please search for the fire chief",
    "look up the mayor of Lyon",
    "look it up",
    "lookup the recall",
    "use the web to check the hours",
    "use tools to find the speech",
    "google the exchange rate",
    "what is in the news",
    "the current price of rice",
    "what happened today",
)
_SKIP = (
    "hi",
    "Hello, how are you?",
    "Good morning.",
    "What is 2+2?",
    "Write a Python function that reverses a string.",
    "Write a four-line poem about rain.",
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
