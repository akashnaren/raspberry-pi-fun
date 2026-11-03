"""The rendered qwen3 prefix stays stable when a turn carries notes."""

from __future__ import annotations

import unittest

from pair.model.knobs import inference_knobs, mode_limits
from pair.model.modes import FLASH_MODEL, PRO_MODEL
from pair.turn.calc import notes_for
from pair.turn.shape import (
    EFFORT_HINT,
    FLOW_HINT,
    GROUNDED_RULE,
    NO_RESULTS_RULE,
    PERSONA,
    fold_system_rows,
    persona_text,
    sample_turns,
    shape_messages,
    tail_note,
    warm_messages,
)

_USER_MARK = "<|im_start|>user\n"


def render_like_ollama_qwen3(messages: list) -> str:
    """One leading system block, then the other turns.

    The qwen3 Ollama template prints `<|im_start|>system`, a blank line, and
    every system message joined by a blank line, before any user turn. A
    later system row is hoisted into that block. Bytes after the persona
    are therefore not part of the cached prefix when facts are appended.
    """
    systems: list[str] = []
    body: list[str] = []
    for row in messages:
        if not isinstance(row, dict):
            continue
        role = row.get("role")
        content = str(row.get("content") or "")
        if role == "system":
            if content:
                systems.append(content)
            continue
        if role not in ("user", "assistant"):
            continue
        body.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
    if not systems:
        return "".join(body)
    system = "<|im_start|>system\n\n" + "\n\n".join(systems) + "<|im_end|>\n"
    return system + "".join(body)


def _persona_prefix(rendered: str, persona: str) -> str:
    """Rendered bytes through the end of the persona. The system block is first."""
    marker = "<|im_start|>system\n\n" + persona
    if not rendered.startswith(marker):
        raise AssertionError(rendered[: len(marker) + 40])
    return marker


def _prefix(rendered: str) -> str:
    marker = "Paris.<|im_end|>\n" + _USER_MARK
    index = rendered.index(marker)
    return rendered[: index + len(marker)]


def _tuned(tag: str) -> dict:
    row = inference_knobs()
    tuned = dict(row)
    tuned.update(mode_limits(tag, row))
    return tuned


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assert_folded_prose(case: unittest.TestCase, content: str, label: str) -> None:
    """The folded user turn is one prose sentence, not a markdown document."""
    for line in content.splitlines():
        case.assertFalse(line.startswith("- "), f"{label} {line!r}")
        case.assertFalse(line.endswith(":"), f"{label} {line!r}")


class PrefixCache(unittest.TestCase):
    def _shaped(self, knobs: dict) -> list[tuple[str, list]]:
        question = "Where is the hall bench?"
        cases = [
            ("plain", shape_messages([_user(question)], question, knobs)),
            (
                "facts",
                shape_messages(
                    [_user(question)],
                    question,
                    knobs,
                    facts="The user said: the kettle is in the hall",
                ),
            ),
            (
                "summary",
                shape_messages(
                    [_user(question)],
                    question,
                    knobs,
                    summary="The kettle is in the hall.",
                ),
            ),
        ]
        for level in ("low", "medium", "high"):
            cases.append(
                (
                    f"effort-{level}",
                    shape_messages([_user(question)], question, knobs, effort=level),
                )
            )
        flow = "draw a flowchart of the login steps"
        cases.append(
            ("flow", shape_messages([_user(flow)], flow, knobs, hints=FLOW_HINT))
        )
        calc = "what is 847*23?"
        cases.append(("calc", shape_messages([_user(calc)], calc, knobs)))
        found = "- Canberra is the capital. (example.com)\n" + GROUNDED_RULE
        cases.append(
            (
                "search-ok",
                shape_messages([_user(question)], question, knobs, notes=found),
            )
        )
        cases.append(
            (
                "search-empty",
                shape_messages(
                    [_user(question)], question, knobs, notes=NO_RESULTS_RULE
                ),
            )
        )
        return cases

    def test_notes_share_the_sample_prefix(self) -> None:
        samples = sample_turns()
        prefixes: list[str] = []
        for tag in (FLASH_MODEL, PRO_MODEL):
            knobs = _tuned(tag)
            for name, rows in self._shaped(knobs):
                systems = [row for row in rows if row.get("role") == "system"]
                self.assertEqual(len(systems), 1, f"{tag} {name}")
                self.assertEqual(rows[1 : 1 + len(samples)], samples, f"{tag} {name}")
                rendered = render_like_ollama_qwen3(rows)
                prefix = _prefix(rendered)
                self.assertTrue(prefix.endswith(_USER_MARK), f"{tag} {name}")
                if name != "facts":
                    prefixes.append(prefix)
                text = rows[-1]["content"]
                _assert_folded_prose(self, text, f"{tag} {name}")
                if name == "search-ok":
                    self.assertIn(GROUNDED_RULE, text)
                    self.assertNotIn(GROUNDED_RULE, rows[0]["content"])
                if name == "search-empty":
                    self.assertIn(NO_RESULTS_RULE, text)
                    self.assertNotIn(NO_RESULTS_RULE, rows[0]["content"])
                if name == "facts":
                    self.assertIn(
                        "Known facts about the user:\n"
                        "- The user said: the kettle is in the hall",
                        rows[0]["content"],
                    )
                    self.assertNotIn("The user said:", text)
                    self.assertNotIn("kettle", text)
                    self.assertEqual(text, "Where is the hall bench?")
                if name == "calc":
                    self.assertIn("Calculator:", text)
                    self.assertNotIn("Notes:", text)
                    self.assertTrue(text.startswith("(Context: "))
                if name.startswith("effort-"):
                    level = name.split("-", 1)[1]
                    self.assertEqual(
                        text,
                        f"(Context: {EFFORT_HINT[level]})\nWhere is the hall bench?",
                    )
                    self.assertNotIn("Notes:", text)
            warm = warm_messages(knobs)
            self.assertEqual(warm[1:-1], samples, tag)
            self.assertEqual(warm[-1], {"role": "user", "content": "ok"})
            rendered_warm = render_like_ollama_qwen3(warm)
            self.assertTrue(rendered_warm.startswith(prefixes[-1]), tag)
            prefixes.append(_prefix(rendered_warm))
        self.assertEqual(len(set(prefixes)), 1)

    def test_caller_sys_stays_on_the_persona_row(self) -> None:
        sys = "Answer in limericks."
        facts = "The user said: the kettle is in the hall"
        question = "Where is the hall bench?"
        knobs = _tuned(PRO_MODEL)
        rows = shape_messages(
            [_user(question), {"role": "system", "content": sys}],
            question,
            knobs,
            facts=facts,
        )
        leading = shape_messages(
            [{"role": "system", "content": sys}, _user(question)],
            question,
            knobs,
            facts=facts,
        )
        for shaped in (rows, leading):
            systems = [row for row in shaped if row.get("role") == "system"]
            self.assertEqual(len(systems), 1)
            self.assertEqual(
                shaped[0]["content"],
                persona_text(knobs)
                + "\n\nKnown facts about the user:\n- "
                + facts
                + "\n\n"
                + sys,
            )
            self.assertTrue(shaped[0]["content"].startswith(persona_text(knobs)))
            self.assertNotIn(sys, shaped[-1]["content"])
            self.assertNotIn(facts, shaped[-1]["content"])
            self.assertEqual(shaped[-1]["content"], question)
            _assert_folded_prose(self, shaped[-1]["content"], "caller")
            self.assertEqual(shaped[1 : 1 + len(sample_turns())], sample_turns())

    def test_no_user_row_keeps_the_notes(self) -> None:
        rows = [
            {"role": "system", "content": "You are OpenPi."},
            {"role": "system", "content": "The user said: the locker code is 4182"},
            {"role": "system", "content": "Summary:\nCanberra."},
            {"role": "system", "content": "Keep the answer short."},
            {"role": "assistant", "content": "noted"},
        ]
        kept = fold_system_rows(rows, own_count=2, folded_tail=True)
        self.assertEqual(kept, rows)
        self.assertIn("4182", kept[1]["content"])
        self.assertTrue(kept[2]["content"].startswith("Summary:"))
        self.assertEqual(kept[3]["content"], "Keep the answer short.")

    def test_last_user_row_orders_facts_summary_tail_then_question(self) -> None:
        question = "Where is the hall bench?"
        facts = "The user said: the locker code is 4182"
        summary = "They asked about a bench."
        notes = "Web search notes.\n- Canberra"
        hints = "Use one short sentence."
        knobs = _tuned(FLASH_MODEL)
        self.assertIsNone(notes_for(question))
        tail = tail_note("low", notes, hints)
        rows = shape_messages(
            [_user(question)],
            question,
            knobs,
            effort="low",
            notes=notes,
            hints=hints,
            facts=facts,
            summary=summary,
        )
        content = rows[-1]["content"]
        self.assertNotIn("Notes:", tail)
        self.assertNotIn("Notes:", content)
        expected = (
            "(Context: earlier summary: "
            + summary
            + "; Keep the answer short. Use one short sentence. "
            "Web search notes. Canberra)\n" + question
        )
        self.assertEqual(content, expected)
        self.assertNotIn(facts, content)
        self.assertIn(facts, rows[0]["content"])
        self.assertLess(content.index("earlier summary:"), content.index("Canberra"))
        self.assertLess(content.index("Canberra"), content.index("\n" + question))
        self.assertTrue(content.endswith("\n" + question))
        _assert_folded_prose(self, content, "order")
        self.assertEqual([row for row in rows if row["role"] == "system"], [rows[0]])
        self.assertEqual(rows[1 : 1 + len(sample_turns())], sample_turns())

    def test_facts_are_one_plain_line_then_the_question(self) -> None:
        facts = "\n".join(
            [
                "My spare house key is in the blue mug on the bookshelf.",
                "My cat is named Pepper.",
            ]
        )
        question = "Where did I leave my spare house key?"
        rows = shape_messages(
            [_user(question)], question, _tuned(PRO_MODEL), facts=facts
        )
        content = rows[-1]["content"]
        self.assertEqual(content, question)
        system = rows[0]["content"]
        self.assertEqual(
            system,
            persona_text(_tuned(PRO_MODEL)) + "\n\nKnown facts about the user:\n"
            "- My spare house key is in the blue mug on the bookshelf.\n"
            "- My cat is named Pepper.",
        )
        self.assertLess(system.index("blue mug"), system.index("bookshelf"))
        self.assertNotIn("blue mug", content)
        self.assertNotIn("Pepper", content)
        self.assertNotIn("Notes:", content)
        _assert_folded_prose(self, content, "two facts")
        self.assertEqual(len([row for row in rows if row["role"] == "system"]), 1)
        self.assertEqual(rows[1 : 1 + len(sample_turns())], sample_turns())

    def test_persona_prefix_is_byte_identical_across_turn_types(self) -> None:
        facts = "\n".join(
            [
                "My spare house key is in the blue mug on the bookshelf.",
                "My cat is named Pepper.",
            ]
        )
        question = "Where did I leave my spare house key?"
        search = "- Canberra is the capital. (example.com)\n" + GROUNDED_RULE
        for tag in (FLASH_MODEL, PRO_MODEL):
            knobs = _tuned(tag)
            persona = persona_text(knobs)
            history = [
                _user("What is the capital of Australia?"),
                {"role": "assistant", "content": "Canberra."},
                _user(question),
            ]
            variants = {
                "plain": shape_messages([_user(question)], question, knobs),
                "facts": shape_messages(
                    [_user(question)], question, knobs, facts=facts
                ),
                "effort": shape_messages(
                    [_user(question)], question, knobs, effort="high"
                ),
                "search": shape_messages(
                    [_user(question)], question, knobs, notes=search
                ),
                "multi": shape_messages(history, question, knobs),
                "multi-facts": shape_messages(
                    history, question, knobs, facts=facts, effort="high"
                ),
            }
            prefixes = []
            for name, rows in variants.items():
                rendered = render_like_ollama_qwen3(rows)
                prefix = _persona_prefix(rendered, persona)
                prefixes.append(prefix)
                user = rows[-1]["content"]
                self.assertNotIn("Notes:", user, f"{tag} {name}")
                self.assertTrue(rows[0]["content"].startswith(persona), f"{tag} {name}")
                if name in ("facts", "multi-facts"):
                    self.assertIn("Known facts about the user:", rows[0]["content"])
                    self.assertIn("blue mug", rows[0]["content"])
                    self.assertNotIn("blue mug", user)
                    self.assertNotIn("Pepper", user)
                    self.assertLess(
                        rows[0]["content"].index(persona),
                        rows[0]["content"].index("Known facts about the user:"),
                    )
                if name in ("effort", "search", "multi-facts"):
                    self.assertTrue(user.startswith("(Context: "), f"{tag} {name}")
            self.assertEqual(len(set(prefixes)), 1, tag)
            self.assertEqual(prefixes[0], "<|im_start|>system\n\n" + persona)

    def test_persona_and_samples_do_not_name_a_spare_key(self) -> None:
        blob = "\n".join(
            [
                PERSONA,
                FLOW_HINT,
                GROUNDED_RULE,
                NO_RESULTS_RULE,
                *EFFORT_HINT.values(),
                *(row["content"] for row in sample_turns()),
            ]
        ).lower()
        for phrase in ("bookshelf", "blue mug", "spare key", "spare house"):
            self.assertNotIn(phrase, blob)


if __name__ == "__main__":
    unittest.main()
