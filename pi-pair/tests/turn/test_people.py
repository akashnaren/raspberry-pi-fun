"""Recorded search hits for one person, before a note is built."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from pair.routes.chat import _prompt_note
from pair.routes.search import _with_search
from pair.turn.abilities import clean_reply
from pair.turn.people import person_note
from pair.turn.search_ask import explicit_search, name_context, search_query
from pair.turn.shape import GROUNDED_RULE, PERSONA, estimate_tokens, shape_messages

# DuckDuckGo-shaped hits for one query that names an organization.
# The etched page uses a first name plus an initial. The other two are
# different people who share a short name.
RECORDED = [
    {
        "title": "Sid Nair",
        "url": "https://sidnair.com/gallery",
        "snippet": "Gallery of a ceramic artist.",
    },
    {
        "title": "Siddhartha N. – Member of Technical Staff at Etched",
        "url": "https://example.com/team/siddhartha",
        "snippet": "Siddhartha N. works on inference hardware.",
    },
    {
        "title": "Sid Nair - Head of Advanced Packaging",
        "url": "https://etched.example/sid-nair",
        "snippet": "Sid Nair leads advanced packaging programs.",
    },
]


def _packed(hits: list[dict]) -> str:
    lines = ["Web search notes."]
    for hit in hits:
        lines.append(f"- {hit['title']} ({hit['url']}): {hit['snippet']}")
    return "\n".join(lines)


class PeopleTests(unittest.TestCase):
    def test_name_context_splits_the_org_and_skips_cues(self):
        self.assertEqual(
            name_context("Hello who is sid nair etched"),
            (["sid", "nair"], ["etched"]),
        )
        self.assertEqual(
            name_context("Who is Sid Nair at Etched?"),
            (["Sid", "Nair"], ["Etched"]),
        )
        self.assertEqual(name_context("who is ada lovelace"), (["ada", "lovelace"], []))
        self.assertEqual(name_context("who is better, cats or dogs"), ([], []))
        self.assertEqual(name_context("who is my dog"), ([], []))
        self.assertEqual(
            name_context("Who is Sid Nair - use your tools web search"),
            ([], []),
        )
        self.assertFalse(explicit_search("who is better, cats or dogs"))
        self.assertFalse(explicit_search("who is my dog"))
        self.assertEqual(
            search_query("Hello who is sid nair etched"), "sid nair etched"
        )

    def test_recorded_hits_keep_the_org_match_only(self):
        found = person_note("Hello who is sid nair etched", RECORDED)
        self.assertIsNotNone(found)
        assert found is not None
        note = found["prompt_note"]
        self.assertTrue(found["brief"])
        self.assertLessEqual(len(note), 160)
        self.assertLessEqual(estimate_tokens(note), 40)
        self.assertIn("etched", note.lower())
        self.assertIn("Technical Staff", note)
        self.assertNotIn("ceramic", note.lower())
        self.assertNotIn("packaging", note.lower())
        self.assertNotIn("sidnair.com", note.lower())
        self.assertFalse(note.startswith("- "))
        self.assertNotRegex(note, r"(?m)^- ")
        self.assertEqual(
            [hit["title"] for hit in found["hits"]],
            ["Siddhartha N. – Member of Technical Staff at Etched"],
        )

    def test_a_long_snippet_stays_inside_the_brief_cap(self):
        hits = [
            {
                "title": "Siddhartha N. – Member of Technical Staff at Etched",
                "url": "https://example.com/team",
                "snippet": "inference " * 80,
            }
        ]
        found = person_note("who is sid nair etched", hits)
        assert found is not None
        self.assertLessEqual(len(found["prompt_note"]), 160)
        self.assertIn("etched", found["prompt_note"].lower())

    def test_no_org_match_drops_every_other_person(self):
        hits = [
            {
                "title": "Sid Nair",
                "url": "https://sidnair.com/",
                "snippet": "Gallery of a ceramic artist.",
            }
        ]
        found = person_note("who is sid nair etched", hits)
        assert found is not None
        self.assertIn("etched", found["prompt_note"].lower())
        self.assertNotIn("ceramic", found["prompt_note"].lower())
        self.assertEqual(found["hits"], [])

    def test_no_context_token_keeps_a_matching_name(self):
        hits = [
            {
                "title": "Ada Lovelace",
                "url": "https://example.com/ada",
                "snippet": "Mathematician and writer.",
            }
        ]
        found = person_note("who is ada lovelace", hits)
        assert found is not None
        self.assertIn("Lovelace", found["prompt_note"])
        self.assertIn("Mathematician", found["prompt_note"])
        self.assertNotIn("No result mentions", found["prompt_note"])

    def test_split_families_are_not_merged(self):
        hits = [
            {
                "title": "Alex Kim – Engineer at Etched",
                "url": "https://example.com/kim",
                "snippet": "Alex Kim is an engineer at Etched.",
            },
            {
                "title": "Alex Rivera – Designer at Etched",
                "url": "https://example.com/rivera",
                "snippet": "Alex Rivera is a designer at Etched.",
            },
        ]
        found = person_note("who is alex at etched", hits)
        assert found is not None
        note = found["prompt_note"]
        self.assertIn("do not merge", note.lower())
        present = [name for name in ("Kim", "Rivera") if name in note]
        self.assertEqual(len(present), 1, note)
        self.assertEqual(len(found["hits"]), 1)

    def test_an_explicit_lookup_is_not_a_person_note(self):
        self.assertIsNone(person_note("look up ada lovelace", RECORDED))
        self.assertIsNone(person_note("who are you", RECORDED))

    def test_brief_note_skips_the_long_grounding_rule(self):
        found = person_note("Hello who is sid nair etched", RECORDED)
        assert found is not None
        shown = _prompt_note(
            {"status": "ok", "brief": True, "prompt_note": found["prompt_note"]}
        )
        self.assertEqual(shown, found["prompt_note"])
        self.assertNotIn(GROUNDED_RULE, shown)
        self.assertNotIn("Never guess", shown)
        rows = shape_messages(
            [{"role": "user", "content": "Hello who is sid nair etched"}],
            "Hello who is sid nair etched",
            notes=shown,
        )
        self.assertEqual(rows[0]["content"], PERSONA)
        self.assertNotIn("etched", rows[0]["content"].lower())
        plot_at = next(i for i, row in enumerate(rows) if "```plot" in row["content"])
        self.assertLess(plot_at, len(rows) - 2)
        self.assertEqual(rows[-1]["role"], "user")
        self.assertIn("etched", rows[-2]["content"].lower())
        self.assertNotIn(GROUNDED_RULE, rows[-2]["content"])
        self.assertNotIn("Never guess", rows[-2]["content"])
        self.assertFalse(rows[-2]["content"].startswith("Notes:"))

    def test_pasted_result_lines_drop_when_a_search_ran(self):
        reply = (
            "Siddhartha N. is a Member of Technical Staff at Etched.\n"
            "- sidnair.com: Gallery of ceramic work\n"
            "- Sid Nair (sidnair.com): Gallery"
        )
        cleaned = clean_reply(reply, "who is sid nair etched", have_tools=True)
        self.assertIn("Etched", cleaned)
        self.assertNotIn("sidnair.com", cleaned)
        self.assertNotIn("Gallery", cleaned)
        only = "- sidnair.com: Gallery"
        self.assertIn(
            "sidnair.com",
            clean_reply(only, "who is sid nair", have_tools=True),
        )
        shopping = "- milk\n- eggs"
        self.assertEqual(
            clean_reply(shopping, "shopping list", have_tools=False),
            shopping,
        )

    def test_with_search_builds_the_brief_note_from_packed_hits(self):
        def fake(query, opener=None, limit=None):
            del opener, limit
            self.assertIn("etched", query)
            return {
                "status": "ok",
                "sources": [
                    {"title": hit["title"], "url": hit["url"]} for hit in RECORDED
                ],
                "context": _packed(RECORDED),
            }

        with (
            patch("pair.routes.search.lookup_web", fake),
            patch("pair.mesh.offload.remote_search_enabled", return_value=False),
        ):
            _messages, note = _with_search([], "Hello who is sid nair etched", "flash")
        self.assertEqual(note["status"], "ok")
        self.assertTrue(note["brief"])
        self.assertLessEqual(len(note["prompt_note"]), 160)
        self.assertNotIn("ceramic", note["prompt_note"].lower())
        self.assertNotIn("packaging", note["prompt_note"].lower())
        self.assertEqual(len(note["sources"]), 1)
        shown = _prompt_note(note)
        self.assertNotIn(GROUNDED_RULE, shown)


if __name__ == "__main__":
    unittest.main()
