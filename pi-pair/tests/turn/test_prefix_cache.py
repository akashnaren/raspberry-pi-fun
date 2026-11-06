"""The rendered qwen3 prefix stays stable when a turn carries notes."""

from __future__ import annotations

import unittest

from pair.ingest.docfit import DOC_HINT
from pair.model.knobs import tuned_knobs
from pair.model.modes import FLASH_MODEL, PRO_MODEL
from pair.turn.calc import notes_for
from pair.turn.shape import (
    EFFORT_HINT,
    FLOW_HINT,
    GROUNDED_RULE,
    NO_RESULTS_RULE,
    PERSONA,
    estimate_tokens,
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
    return tuned_knobs(tag)


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _tail_case(prompt, messages, last, edited_last, common=None, **extra):
    """One Pro facts-tail row. `common` fills the shared keys, then `extra`."""
    row = {
        "prompt": prompt,
        "messages": messages,
        "last": last,
        "edited_last": edited_last,
        "effort": "",
        "system": "",
        "facts": "",
        "edited": "",
        "knobs": None,
        "limit": 0,
        "kept": "",
        "stable": True,
        "summary": "",
        "samples": True,
        "header": None,
    }
    row.update(common or {})
    row.update(extra)
    return row


def _check_tail(test, case, samples):
    prompt = case["prompt"]
    knobs = case["knobs"]

    def turn(fact):
        return shape_messages(
            case["messages"],
            prompt,
            knobs,
            facts=fact,
            effort=case["effort"],
            summary=case["summary"],
        )

    before, after = turn(case["facts"]), turn(case["edited"])
    text, edit = before[-1]["content"], after[-1]["content"]
    head = before[0]["content"]
    counted = sum(estimate_tokens(row["content"]) for row in before)
    limit = case["limit"] or knobs["num_ctx"]
    kept = case["kept"]
    width = len(samples)
    test.assertEqual(text, case["last"] or text, prompt)
    test.assertEqual(edit, case["edited_last"] or edit, prompt)
    on = "Known facts about the user:" in text
    want = case["header"]
    test.assertEqual(on, want if want is not None else on, prompt)
    test.assertEqual(head == after[0]["content"], case["stable"], prompt)
    test.assertEqual(head, case["system"] or head, prompt)
    test.assertLessEqual(counted, limit, prompt)
    test.assertTrue(not kept or {"role": "user", "content": kept} in before, prompt)
    test.assertEqual(before[1 : 1 + width] == samples, case["samples"], prompt)


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
                    bullet = (
                        "Known facts about the user:\n"
                        "- The user said: the kettle is in the hall"
                    )
                    self.assertEqual(text, "Where is the hall bench?")
                    self.assertNotIn("The user said:", text)
                    self.assertNotIn("kettle", text)
                    if tag == PRO_MODEL:
                        self.assertNotIn(
                            "Known facts about the user:", rows[0]["content"]
                        )
                        self.assertNotIn("kettle", rows[0]["content"])
                    else:
                        self.assertIn(bullet, rows[0]["content"])
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
                persona_text(knobs) + "\n\n" + sys,
            )
            self.assertNotIn("Known facts about the user:", shaped[0]["content"])
            self.assertNotIn(facts, shaped[0]["content"])
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
        block = (
            "Known facts about the user:\n"
            "- My spare house key is in the blue mug on the bookshelf.\n"
            "- My cat is named Pepper."
        )
        self.assertEqual(content, block + "\n\n" + question)
        system = rows[0]["content"]
        self.assertEqual(system, persona_text(_tuned(PRO_MODEL)))
        self.assertLess(content.index("blue mug"), content.index("bookshelf"))
        self.assertNotIn("blue mug", system)
        self.assertNotIn("Pepper", system)
        self.assertNotIn("Notes:", content)
        self.assertIn("\n- My cat is named Pepper.", content)
        self.assertEqual(len([row for row in rows if row["role"] == "system"]), 1)
        self.assertEqual(rows[1 : 1 + len(sample_turns())], sample_turns())

    def test_pro_facts_tail_before_a_first_person_turn(self) -> None:
        """Facts leave system row 0 and lead only a first-person final row."""
        knobs = _tuned(PRO_MODEL)
        persona = persona_text(knobs)
        facts = "The kettle is in the hall.\nThe cat is named Pepper."
        edited = "The spare key is in the blue mug."
        block = (
            "Known facts about the user:\n- The kettle is in the hall.\n"
            "- The cat is named Pepper."
        )
        edited_block = "Known facts about the user:\n- " + edited
        kettle = "Where did I leave my kettle?"
        story = "write a story about a hero"
        context = "(Context: Give a moderate amount of detail.)"
        sample = sample_turns()[2]["content"]
        one = "Known facts about the user:\n- The kettle is in the hall."
        caller = {"role": "system", "content": "Note " + ("detail " * 800)}
        shared = dict(_tuned(FLASH_MODEL))
        shared["pro_model"] = shared["model"]
        bench = "Where is the hall bench?"
        key = "Where did I leave my spare house key?"
        spots = "\n".join(
            "The user said: spare key spot %s is the blue mug on the bookshelf." % index
            for index in range(40)
        )
        notes = [
            {"role": "system", "content": "Caller note %s " % index + ("detail " * 40)}
            for index in range(3)
        ]
        common = {"knobs": knobs, "facts": facts, "edited": edited, "system": persona}
        hall = {"role": "assistant", "content": "In the hall."}
        lead, edit = block + "\n\n" + kettle, edited_block + "\n\n" + kettle
        ctx = block + "\n\n" + context + "\n" + kettle
        ctx_edit = edited_block + "\n\n" + context + "\n" + kettle
        cap = knobs["num_ctx"]
        pump = ("cycle " * 2000) + "Why does my pump keep cycling?"
        specs = (
            (kettle, [_user(kettle)], lead, edit, {}),
            (story, [_user(story)], story, story, {}),
            (
                kettle,
                [_user(kettle), hall],
                "In the hall.",
                "In the hall.",
                {"kept": kettle},
            ),
            (
                bench,
                [_user(bench)],
                bench,
                bench,
                {
                    "knobs": shared,
                    "system": persona_text(shared) + "\n\n" + block,
                    "stable": False,
                },
            ),
            (kettle, [_user(kettle)], ctx, ctx_edit, {"effort": "medium"}),
            (
                sample,
                [_user(kettle), hall, _user(sample)],
                one + "\n\n" + sample,
                edited_block + "\n\n" + sample,
                {"facts": "The kettle is in the hall.", "kept": kettle},
            ),
            (kettle, [caller, _user(kettle)], lead, edit, {"system": "", "limit": cap}),
            (
                story,
                [caller, _user(story)],
                story,
                story,
                {"edited": "", "system": "", "limit": cap},
            ),
            (
                key,
                [{"role": "system", "content": DOC_HINT}, _user(key)],
                "",
                "",
                {
                    "knobs": dict(knobs, num_ctx=400),
                    "facts": spots,
                    "edited": spots,
                    "system": "",
                    "summary": "They asked about the spare key.",
                    "limit": 400,
                    "samples": False,
                    "header": True,
                },
            ),
            (
                kettle,
                notes + [_user(kettle)],
                "",
                "",
                {
                    "knobs": dict(knobs, num_ctx=544),
                    "edited": facts,
                    "system": "",
                    "limit": 544,
                    "samples": False,
                    "header": True,
                },
            ),
            ("pump", [_user(pump)], "", "", {"header": True, "samples": False}),
            ("hero", [_user(story)], "", "", {"header": False, "summary": "I asked."}),
        )
        samples = sample_turns()
        for prompt, messages, last, edited_last, extra in specs:
            _check_tail(
                self,
                _tail_case(prompt, messages, last, edited_last, common, **extra),
                samples,
            )

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
                    if tag == PRO_MODEL:
                        self.assertEqual(rows[0]["content"], persona)
                        self.assertNotIn(
                            "Known facts about the user:", rows[0]["content"]
                        )
                        self.assertIn("Known facts about the user:", user)
                        self.assertIn("blue mug", user)
                        self.assertIn("Pepper", user)
                        self.assertLess(
                            user.index("Known facts about the user:"),
                            user.index(question),
                        )
                    else:
                        self.assertIn("Known facts about the user:", rows[0]["content"])
                        self.assertIn("blue mug", rows[0]["content"])
                        self.assertNotIn("blue mug", user)
                        self.assertNotIn("Pepper", user)
                        self.assertLess(
                            rows[0]["content"].index(persona),
                            rows[0]["content"].index("Known facts about the user:"),
                        )
                if name in ("effort", "search", "multi-facts"):
                    if tag == PRO_MODEL and name == "multi-facts":
                        self.assertLess(
                            user.index("Known facts about the user:"),
                            user.index("(Context: "),
                        )
                    else:
                        self.assertTrue(user.startswith("(Context: "), f"{tag} {name}")
            self.assertEqual(len(set(prefixes)), 1, tag)
            self.assertEqual(prefixes[0], "<|im_start|>system\n\n" + persona)

    def test_caller_rows_sit_between_the_persona_and_the_facts(self) -> None:
        facts = "My spare house key is in the blue mug on the bookshelf."
        question = "Where did I leave my spare house key?"
        knobs = _tuned(PRO_MODEL)
        persona = persona_text(knobs)
        rows = shape_messages(
            [
                {"role": "system", "content": DOC_HINT},
                _user(question),
            ],
            question,
            knobs,
            facts=facts,
        )
        system = rows[0]["content"]
        user = rows[-1]["content"]
        self.assertEqual(system, persona + "\n\n" + DOC_HINT)
        self.assertNotIn("Known facts about the user:", system)
        self.assertTrue(user.startswith("Known facts about the user:\n- " + facts))
        self.assertTrue(user.endswith("\n\n" + question))
        rendered = render_like_ollama_qwen3(rows)
        self.assertTrue(rendered.startswith("<|im_start|>system\n\n" + persona))
        self.assertLess(rendered.index(DOC_HINT), rendered.index("Known facts"))
        self.assertLess(rendered.index("Known facts"), rendered.index(question))

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
