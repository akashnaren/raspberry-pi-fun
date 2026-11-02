"""The rendered qwen3 prefix stays stable when a turn carries notes."""

from __future__ import annotations

import unittest

from pair.model.knobs import inference_knobs, mode_limits
from pair.model.modes import FLASH_MODEL, PRO_MODEL
from pair.turn.calc import notes_for
from pair.turn.shape import (
    EFFORT_HINT,
    FACTS_LABEL,
    FLOW_HINT,
    GROUNDED_RULE,
    NO_RESULTS_RULE,
    QUESTION_LABEL,
    fold_system_rows,
    persona_text,
    sample_turns,
    shape_messages,
    tail_note,
    warm_messages,
)

_USER_MARK = "<|im_start|>user\n"


def render_like_ollama_qwen3(messages: list) -> str:
    """Merge every system row into one top block, then render the other turns."""
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
    system = ""
    if systems:
        system = "<|im_start|>system\n" + "\n\n".join(systems) + "<|im_end|>\n"
    return system + "".join(body)


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
                prefixes.append(prefix)
                if name == "search-ok":
                    self.assertIn(GROUNDED_RULE, rows[-1]["content"])
                    self.assertNotIn(GROUNDED_RULE, rows[0]["content"])
                if name == "search-empty":
                    self.assertIn(NO_RESULTS_RULE, rows[-1]["content"])
                    self.assertNotIn(NO_RESULTS_RULE, rows[0]["content"])
                if name == "facts":
                    self.assertTrue(
                        rows[-1]["content"].startswith(FACTS_LABEL + "\n- "),
                        f"{tag} {name}",
                    )
                    self.assertNotIn(FACTS_LABEL, rows[0]["content"])
                if name == "calc":
                    self.assertIn("Calculator:", rows[-1]["content"])
                if name.startswith("effort-"):
                    level = name.split("-", 1)[1]
                    text = rows[-1]["content"]
                    self.assertLess(
                        text.index(EFFORT_HINT[level]),
                        text.index(QUESTION_LABEL),
                        f"{tag} {name}",
                    )
                    self.assertTrue(
                        text.endswith(QUESTION_LABEL + " Where is the hall bench?"),
                        f"{tag} {name}",
                    )
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
            self.assertEqual(shaped[0]["content"], persona_text(knobs) + "\n\n" + sys)
            self.assertNotIn(sys, shaped[-1]["content"])
            self.assertNotIn(FACTS_LABEL, shaped[0]["content"])
            self.assertTrue(shaped[-1]["content"].startswith(FACTS_LABEL + "\n- "))
            self.assertIn(facts, shaped[-1]["content"])
            self.assertTrue(
                shaped[-1]["content"].endswith(QUESTION_LABEL + " " + question)
            )
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
        labeled = "\n\n".join(
            [
                FACTS_LABEL + "\n- " + facts,
                "Summary:\n" + summary,
                "Notes:\n" + tail,
                QUESTION_LABEL + " " + question,
            ]
        )
        self.assertEqual(FACTS_LABEL, "Known facts about the user:")
        self.assertEqual(QUESTION_LABEL, "Question:")
        self.assertEqual(content, labeled)
        self.assertLess(content.index(FACTS_LABEL), content.index("Summary:"))
        self.assertLess(content.index("Summary:"), content.index("Notes:"))
        self.assertLess(content.index("Notes:"), content.index(QUESTION_LABEL))
        self.assertTrue(content.endswith(QUESTION_LABEL + " " + question))
        self.assertEqual([row for row in rows if row["role"] == "system"], [rows[0]])
        self.assertEqual(rows[1 : 1 + len(sample_turns())], sample_turns())


if __name__ == "__main__":
    unittest.main()
