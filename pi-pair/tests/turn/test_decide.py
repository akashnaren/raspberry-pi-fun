"""Model search check: the word, the shared prefix, and the fallback."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pair.core.cancel import ClientGone
from pair.model.chat_once import classify_once
from pair.routes.chat import _prompt_note
from pair.turn.decide import (
    DECIDE_PROMPT,
    decide_messages,
    decide_search,
    parse_decision,
    search_candidate,
    web_search_request,
)
from pair.turn.shape import ATTACH_MARK, GROUNDED_RULE, NO_RESULTS_RULE, shape_messages

_PEER = {
    "name": "pi4",
    "host": "127.0.0.1",
    "port": 9,
    "generative": True,
    "role": "brain",
}
_KNOBS = {"num_ctx": 512, "attachment_chars": 1200}


def _ask(reply=None, error=None, cancel=None):
    def fake(*_args, **_kwargs):
        if error is not None:
            raise error
        return reply

    with patch("pair.turn.decide.classify_once", side_effect=fake):
        return decide_search(
            _PEER,
            "ollama",
            "qwen3:0.6b",
            [{"role": "user", "content": "hello"}],
            "hello",
            _KNOBS,
            "",
            "",
            cancel,
        )


class ParseDecision(unittest.TestCase):
    def test_search_skip_and_neither(self):
        self.assertIs(parse_decision("SEARCH"), True)
        self.assertIs(parse_decision(" search."), True)
        self.assertIs(parse_decision("SEAR"), True)
        self.assertIs(parse_decision("SKIP"), False)
        self.assertIs(parse_decision("Skip"), False)
        self.assertIs(parse_decision("<think></think>SKIP"), False)
        self.assertIsNone(parse_decision("hello"))
        self.assertIsNone(parse_decision(""))


class DecideMessages(unittest.TestCase):
    def test_the_prefix_matches_the_answer_and_the_question_stays(self):
        outbound = [{"role": "user", "content": "hello"}]
        fitted = {**_KNOBS, "num_ctx": _KNOBS["num_ctx"] - 128}
        prefix = shape_messages(
            outbound,
            "hello",
            fitted,
            effort="",
            notes="",
            hints="",
            facts="Fact line",
            summary="Sum line",
        )
        rows = decide_messages(outbound, "hello", _KNOBS, "Fact line", "Sum line")
        self.assertEqual(rows[:-1], prefix)
        self.assertEqual(rows[-1], {"role": "user", "content": DECIDE_PROMPT})
        question = "WHERE-IS-THE-ORCHARD-BENCH"
        turns = []
        for index in range(40):
            turns.append({"role": "user", "content": ("old " * 80) + str(index)})
            turns.append({"role": "assistant", "content": "reply " * 80})
        turns.append({"role": "user", "content": question})
        long = decide_messages(turns, question, {"num_ctx": 256}, "", "")
        blob = "\n".join(row["content"] for row in long)
        self.assertIn(question, blob)
        self.assertEqual(long[-1]["content"], DECIDE_PROMPT)


class ClassifyOnce(unittest.TestCase):
    def test_three_tokens_at_the_answer_context(self):
        seen = {}

        def post(url, payload, timeout):
            seen["url"] = url
            seen["payload"] = payload
            seen["timeout"] = timeout
            return {"message": {"content": "SKIP"}}

        knobs = {
            "model": "qwen3:0.6b",
            "pro_model": "qwen3:1.7b",
            "num_ctx": 1800,
            "flash_num_ctx": 2048,
            "pro_num_ctx": 2048,
            "num_thread": 4,
            "flash_num_thread": 4,
            "pro_num_thread": 4,
            "num_batch": 128,
            "flash_num_batch": 128,
            "pro_num_batch": 128,
            "keep_alive": -1,
        }
        with patch("pair.model.chat_once._post_json", side_effect=post):
            text = classify_once(
                _PEER,
                "ollama",
                "qwen3:0.6b",
                [{"role": "user", "content": "x"}],
                knobs,
                3.0,
            )
        self.assertEqual(text, "SKIP")
        options = seen["payload"]["options"]
        self.assertIs(seen["payload"]["think"], False)
        self.assertEqual(options["num_predict"], 3)
        self.assertEqual(options["temperature"], 0)
        self.assertEqual(options["top_k"], 1)
        self.assertEqual(options["stop"], ["\n"])
        self.assertEqual(options["num_ctx"], 2048)
        self.assertEqual(seen["timeout"], 3.0)
        self.assertTrue(seen["url"].endswith("/api/chat"))

    def test_pi2_is_refused(self):
        peer = {
            "name": "pi2",
            "host": "127.0.0.1",
            "port": 9,
            "generative": False,
            "role": "health",
        }
        with self.assertRaises(RuntimeError):
            classify_once(peer, "ollama", "qwen3:0.6b", [], {"num_ctx": 2048}, 1.0)


class DecideFallback(unittest.TestCase):
    def test_a_bad_reply_follows_the_lookup_path(self):
        cases = (TimeoutError("timed out"), RuntimeError("down"), "hello")
        with patch.dict(os.environ, {"PI_PAIR_REMOTE_SEARCH": "0"}):
            for item in cases:
                error = item if isinstance(item, Exception) else None
                reply = None if error is not None else item
                decision = _ask(reply=reply, error=error)
                self.assertTrue(decision.search, item)
                self.assertEqual(decision.source, "fallback")
        down = [{"name": "pi2", "ok": False}]
        up = [{"name": "pi2", "ok": True}]
        with patch.dict(os.environ, {"PI_PAIR_REMOTE_SEARCH": "1"}):
            with patch("pair.turn.decide.snapshot_peers", return_value=down):
                skipped = _ask(reply="hello")
            self.assertFalse(skipped.search)
            self.assertEqual(skipped.source, "fallback")
            with patch("pair.turn.decide.snapshot_peers", return_value=up):
                searched = _ask(error=TimeoutError("timed out"))
            self.assertTrue(searched.search)
            self.assertEqual(searched.source, "fallback")

    def test_a_cancel_is_not_a_fallback(self):
        with self.assertRaises(ClientGone):
            _ask(error=ClientGone())

    def test_a_clear_word_is_the_model(self):
        with patch.dict(os.environ, {"PI_PAIR_REMOTE_SEARCH": "0"}):
            decision = _ask(reply="SKIP")
        self.assertFalse(decision.search)
        self.assertEqual(decision.source, "model")


class SearchCandidate(unittest.TestCase):
    def test_structural_skips_do_not_ask(self):
        calls = []

        def classify(*_args, **_kwargs):
            calls.append(1)
            return "SEARCH"

        cases = (
            ("hi", False, "brain"),
            ("hi", True, "health"),
            ("draw a flowchart of the steps", True, "brain"),
            (f"what does this say{ATTACH_MARK}{'A' * 90}", True, "brain"),
            ("2+2", True, "brain"),
            ("What is 2+2?", True, "brain"),
        )
        with patch("pair.turn.decide.classify_once", side_effect=classify):
            for prompt, mesh, role in cases:
                with patch.dict(os.environ, {"PI_PAIR_ROLE": role}):
                    self.assertFalse(search_candidate(prompt, mesh), prompt)
                    if search_candidate(prompt, mesh):
                        decide_search(
                            _PEER,
                            "ollama",
                            "qwen3:0.6b",
                            [{"role": "user", "content": prompt}],
                            prompt,
                            _KNOBS,
                            "",
                            "",
                            None,
                        )
            with patch.dict(os.environ, {"PI_PAIR_ROLE": "brain"}):
                prompt = "Who keeps the orchard bench?"
                self.assertTrue(search_candidate(prompt, True))
                if search_candidate(prompt, True):
                    decide_search(
                        _PEER,
                        "ollama",
                        "qwen3:0.6b",
                        [{"role": "user", "content": prompt}],
                        prompt,
                        _KNOBS,
                        "",
                        "",
                        None,
                    )
        self.assertEqual(calls, [1])


class Grounding(unittest.TestCase):
    def test_a_lookup_adds_the_rule_and_a_miss_does_not(self):
        ok = _prompt_note({"status": "ok", "prompt_note": "Notes:\nbench"})
        self.assertTrue(ok.endswith(GROUNDED_RULE))
        self.assertIn("bench", ok)
        self.assertEqual(
            _prompt_note({"status": "failed", "prompt_note": "x"}), NO_RESULTS_RULE
        )
        self.assertEqual(_prompt_note(None), "")


class WebSearchFlag(unittest.TestCase):
    def test_only_a_json_boolean_is_accepted(self):
        self.assertFalse(web_search_request({}))
        forced = {"web_search": True, "messages": []}
        self.assertTrue(web_search_request(forced))
        self.assertNotIn("web_search", forced)
        self.assertFalse(web_search_request({"web_search": False}))
        for bad in (1, "true", "yes", None):
            with self.assertRaises(ValueError) as caught:
                web_search_request({"web_search": bad})
            self.assertEqual(str(caught.exception), "web_search must be true or false.")


if __name__ == "__main__":
    unittest.main()
