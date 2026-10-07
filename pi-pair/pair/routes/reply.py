from __future__ import annotations
import json
import threading
import time
from pair.turn.abilities import (
    JSON_RETRY,
    clean_reply,
    mend_cut_tail,
    needs_json_retry,
    settle_blocks,
    tail_hints,
    tool_notes,
)
from pair.turn.assist import is_harmful, scrub_reply, settle_reply, stream_release
from pair.core.cancel import ClientGone
from pair.model.chat_once import chat_llamacpp, chat_ollama
from pair.core.errors import BUSY, WAITING, friendly_error
from pair.flywheel.miss_queue import note_exchange
from pair.model.chat_stream import iter_ollama_channels, stream_llamacpp, stream_ollama
from pair.model.think import peel_think
from pair.turn.shape import (
    public_failure,
    shape_messages,
    structure_hint,
    turns_for_memory,
)
from pair.routes.base import safe_write, status_event, write_event
from pair.routes.chat import _block, _prompt_note, _tuned_knobs, apply_tier
from pair.routes.memory import _memory_prompt
from pair.routes.search import _searched, _source_count
from pair.routes.status import _claim_wait, mode_fields

KEEPALIVE_S = 5.0


class DecodeFailed(Exception):
    """The model did not return an answer. The message is safe to show."""


_LAST_LOCK = threading.Lock()
_LAST = {"prompt": "", "answer": "", "chip": "", "peer": ""}


def remember_completion(prompt: str, answer: str, chip: str, peer: str) -> None:
    text = (prompt or "").strip()
    reply = (answer or "").strip()
    if not text or not reply:
        return
    with _LAST_LOCK:
        _LAST["prompt"] = text
        _LAST["answer"] = reply
        _LAST["chip"] = chip
        _LAST["peer"] = peer


def last_completion() -> dict:
    with _LAST_LOCK:
        return dict(_LAST)


class ReplyRoutes:
    def _policy_refusal(
        self, prompt: str, want_stream: bool, started: float, answer: str
    ) -> None:
        """Fixed refusal from the moderation hook. No model, search, or list hint."""
        remember_completion(prompt, answer, "policy", "policy")
        elapsed = int((time.time() - started) * 1000)
        if not want_stream:
            resp = {
                "id": "pi-pair",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "pi_peer": "policy",
                "pi_chip": "policy",
                "pi_ms": elapsed,
                "pi_model": "policy",
                "pi_kind": "policy",
                "pi_stages": ["answering"],
            }
            apply_tier(self, resp)
            body = json.dumps(resp).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("X-Pi-Peer", "policy")
            self.send_header("X-Pi-Chip", "policy")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", "policy")
        self.send_header("X-Pi-Chip", "policy")
        self.end_headers()
        answering = {"pi_stages": ["answering"]}
        write_event(self, status_event("answering", answering))
        chunk = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }
            ],
            "pi_peer": "policy",
            "pi_chip": "policy",
            "pi_model": "policy",
            "pi_kind": "policy",
            "pi_stages": ["answering"],
        }
        apply_tier(self, chunk)
        safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
        final = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "pi_peer": "policy",
            "pi_chip": "policy",
            "pi_ms": elapsed,
            "pi_model": "policy",
            "pi_kind": "policy",
            "pi_stages": ["answering"],
        }
        apply_tier(self, final)
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _decode_reply(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        search_note: dict | None,
    ) -> tuple[str, str, bool]:
        """One completion. A failed decode is one sentence."""
        meta: dict = {}
        try:
            if kind == "llamacpp":
                content, used = chat_llamacpp(
                    peer,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    meta=meta,
                    cancel=self._cancel,
                )
            else:
                content, used = chat_ollama(
                    peer,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    meta=meta,
                    plan=getattr(self, "_decode_plan", None),
                    cancel=self._cancel,
                )
        except (OSError, json.JSONDecodeError) as error:
            self._last_reasoning = ""
            raise DecodeFailed(public_failure(error)) from error
        self._usage = dict(meta.get("usage") or {})
        content = content or ""
        answer, leaked = peel_think(content)
        reasoning = "\n".join(
            part for part in (str(meta.get("reasoning") or "").strip(), leaked) if part
        )
        refused = _block(reasoning) or _block(answer)
        if refused:
            self._last_reasoning = ""
            return refused, used, False
        self._last_reasoning = reasoning
        content = answer
        if not _block(prompt):
            content = settle_reply(prompt, content, lambda: "")
        refused = _block(content)
        if refused:
            self._last_reasoning = ""
            return refused, used, False
        if not str(content).strip():
            raise DecodeFailed(friendly_error(""))
        content = self._one_more_round(
            peer,
            kind,
            model,
            messages,
            temperature,
            max_tokens,
            prompt,
            search_note,
            content,
        )
        content = clean_reply(
            content,
            prompt,
            _source_count(search_note),
            context=getattr(self, "_local_context", ""),
            have_tools=_searched(search_note),
        )
        cut = (getattr(self, "_usage", {}) or {}).get("done_reason") == "length"
        content = mend_cut_tail(content, cut)
        if not str(content).strip():
            raise DecodeFailed(friendly_error(""))
        return content, used, True

    def _one_more_round(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        search_note: dict | None,
        content: str,
    ) -> str:
        """At most one extra decode: tool results, or a single JSON schema retry."""
        if getattr(self, "_extra_call", False):
            return content
        context = getattr(self, "_local_context", "")
        have = _searched(search_note)
        settled = settle_blocks(content, prompt, context=context, have_tools=have)
        notes = tool_notes(settled, prompt=prompt, context=context, have_tools=have)
        retry = needs_json_retry(prompt, settled) and not notes
        if not notes and not retry:
            return settled
        follow = list(messages or [])
        follow.append({"role": "assistant", "content": settled})
        follow.append(
            {"role": "system", "content": notes or JSON_RETRY},
        )
        self._extra_call = True
        try:
            nxt, _used, _train = self._decode_reply(
                peer,
                kind,
                model,
                follow,
                temperature,
                max_tokens,
                prompt,
                search_note,
            )
        except DecodeFailed:
            return content
        finally:
            self._extra_call = False
        nxt = (nxt or "").strip()
        if not nxt:
            return content
        if retry and not needs_json_retry(prompt, nxt):
            return nxt
        if notes:
            return settled.rstrip() + "\n\n" + nxt
        return settled

    def _stream(
        self,
        peer,
        kind,
        model,
        used,
        messages,
        temperature,
        max_tokens,
        started,
        prompt: str,
        think_name: str = "",
        do_search: bool = False,
        searched: bool = False,
        search_note: dict | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
        ready_answer: str | None = None,
        slot: dict | None = None,
        search_job: dict | None = None,
    ) -> None:  # noqa: C901
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header(
            "X-Pi-Chip", "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        )
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, resident_name)
        self.end_headers()
        self._sse_lock = threading.Lock()
        self._stop_beat = threading.Event()

        def _beat() -> None:
            while not self._stop_beat.wait(KEEPALIVE_S):
                if not safe_write(self, b": keep-alive\n\n", flush=True):
                    return

        threading.Thread(target=_beat, name="sse-keepalive", daemon=True).start()
        stages: list[str] = []
        note = mode_fields(mode_name, route_name, resident_name)

        def emit_status(stage: str, extra: dict | None = None) -> bool:
            if stage not in stages:
                stages.append(stage)
            merged = dict(note)
            if extra:
                merged.update(extra)
            return write_event(self, status_event(stage, merged or None))

        if slot and slot.get("waiting"):
            if not emit_status("waiting", {"pi_detail": WAITING}):
                return

            def on_tick(pos: int, eta: int) -> None:
                emit_status(
                    "waiting",
                    {
                        "pi_detail": WAITING,
                        "pi_queue": {"position": pos, "eta_s": eta},
                    },
                )

            if not _claim_wait(slot, cancel=self._cancel, on_tick=on_tick):
                write_event(
                    self,
                    {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "error": BUSY,
                    },
                )
                safe_write(self, b"data: [DONE]\n\n", flush=True)
                return
            self._mark_queue()

        think_extra = {"pi_think": think_name} if think_name else None
        if not emit_status("thinking", think_extra):
            return
        if do_search:
            self._searched = True
            if not emit_status("searching", {"pi_tool": "search"}):
                return
            if not searched:
                messages, search_note = self._timed_search(
                    messages,
                    prompt,
                    model,
                    getattr(self, "_cancel", None),
                    job=search_job,
                )
            found = {"pi_tool": "search"}
            if search_note:
                found["pi_search"] = search_note["status"]
                found["pi_sources"] = search_note["sources"]
            if not emit_status("searching", found):
                return
        grounded = ready_answer
        answer_extra = dict(think_extra or {})
        if search_note:
            answer_extra["pi_search"] = search_note["status"]
            answer_extra["pi_sources"] = search_note["sources"]
        if not emit_status("answering", answer_extra or None):
            return
        fact_text, summary_text = _memory_prompt(self._memory_scope())
        source_rows = list(messages)
        messages = shape_messages(
            messages,
            prompt,
            _tuned_knobs(model),
            think_name,
            _prompt_note(search_note),
            hints=tail_hints(prompt, structure_hint(prompt) or ""),
            facts=fact_text,
            summary=summary_text,
        )
        self._memory_rows = turns_for_memory(source_rows, messages)
        if grounded is not None:
            self._emit_ready_answer(
                peer,
                kind,
                used,
                prompt,
                grounded,
                started,
                think_name,
                search_note,
                stages,
                mode_name,
                route_name,
                resident_name,
            )
            return
        if not self._public():
            safe_write(
                self,
                f": pi-pair peer={peer['name']} kind={kind} model={used}\n\n".encode(),
                flush=True,
            )
        first = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
            "pi_peer": peer["name"],
            "pi_chip": "brain: pi4" if peer["name"] == "pi4" else peer["name"],
            "pi_model": used,
            "pi_kind": kind,
        }
        if think_name:
            first["pi_think"] = think_name
        if search_note:
            first["pi_search"] = search_note["status"]
            first["pi_sources"] = search_note["sources"]
        first.update(note)
        apply_tier(self, first)
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        parts: list[str] = []
        try:
            plan = getattr(self, "_decode_plan", None)
            if kind == "llamacpp":
                channels = (
                    ("content", delta)
                    for delta in stream_llamacpp(
                        peer,
                        model,
                        messages,
                        temperature,
                        max_tokens,
                        cancel=self._cancel,
                    )
                )
            elif plan is not None:
                channels = iter_ollama_channels(
                    peer,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    plan=plan,
                    cancel=self._cancel,
                    usage=self._usage,
                )
            else:
                channels = (
                    ("content", delta)
                    for delta in stream_ollama(
                        peer,
                        model,
                        messages,
                        temperature,
                        max_tokens,
                        plan=plan,
                        cancel=self._cancel,
                        usage=self._usage,
                    )
                )
            closed = False
            held = True
            policy = ""
            flushed = 0
            thinking_parts: list[str] = []
            thinking_flushed = 0
            reasoning_sent = False

            def write_json(payload: dict) -> bool:
                return safe_write(
                    self, f"data: {json.dumps(payload)}\n\n".encode(), flush=True
                )

            def clear_reasoning() -> bool:
                nonlocal reasoning_sent
                if not reasoning_sent:
                    return True
                reasoning_sent = False
                return write_json(
                    {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                        "pi_reasoning_clear": True,
                    }
                )

            def emit_reasoning(piece: str) -> bool:
                nonlocal reasoning_sent
                if not piece:
                    return True
                ok = write_json(
                    {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning_content": piece},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
                if ok:
                    reasoning_sent = True
                return ok

            for channel, delta in channels:
                if not delta:
                    continue
                if channel == "thinking":
                    nxt = "".join(thinking_parts) + delta
                    release = stream_release(nxt)
                    if release == "refuse":
                        # The span that completes the match is not written.
                        # A thought prefix that already went out is cleared.
                        policy = _block(nxt)
                        thinking_parts.clear()
                        if not clear_reasoning():
                            closed = True
                        break
                    thinking_parts.append(delta)
                    joined_thought = "".join(thinking_parts)
                    piece = joined_thought[thinking_flushed:]
                    thinking_flushed = len(joined_thought)
                    if piece and not emit_reasoning(piece):
                        closed = True
                        break
                    continue
                parts.append(delta)
                joined = "".join(parts)
                release = stream_release(joined)
                if release == "refuse":
                    policy = _block(joined)
                    parts.clear()
                    thinking_parts.clear()
                    if not clear_reasoning():
                        closed = True
                    break
                old = flushed
                if "<" in joined and "think" in joined.lower():
                    visible, leaked = peel_think(joined)
                    if leaked and stream_release(leaked) == "refuse":
                        policy = _block(leaked)
                        parts.clear()
                        thinking_parts.clear()
                        flushed = len(joined) if old else 0
                        if not clear_reasoning():
                            closed = True
                        break
                    prior, _prior_leak = peel_think(joined[:old])
                    piece = visible[len(prior) :] if visible.startswith(prior) else ""
                    flushed = len(joined)
                    if piece:
                        held = False
                else:
                    piece = joined[old:]
                    flushed = len(joined)
                    held = False
                if not piece:
                    continue
                if not getattr(self, "_ttft_ms", 0):
                    self._ttft_ms = max(0, int((time.time() - started) * 1000))
                chunk = {
                    "id": "pi-pair",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": piece},
                            "finish_reason": None,
                        }
                    ],
                }
                if not write_json(chunk):
                    closed = True
                    break
            if not closed and not policy:
                raw_answer = "".join(parts)
                if "<" in raw_answer and "think" in raw_answer.lower():
                    visible, leaked = peel_think(raw_answer)
                    if leaked and stream_release(leaked) == "refuse":
                        policy = _block(leaked)
                        parts.clear()
                        thinking_parts.clear()
                        if not clear_reasoning():
                            closed = True
                    else:
                        parts[:] = [visible] if visible else []
                rest = "".join(thinking_parts)[thinking_flushed:]
                if rest and not emit_reasoning(rest):
                    closed = True
            self._last_reasoning = "" if policy else "".join(thinking_parts)
            if closed:
                answer = "".join(parts).strip()
                if not is_harmful(prompt):
                    answer = scrub_reply(answer) or answer
                if answer:
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    note_exchange(
                        prompt, answer, chip=chip, peer=peer["name"], train=True
                    )
                    remember_completion(prompt, answer, chip, peer["name"])
                return
            if policy:
                answer = policy
                refused = {
                    "id": "pi-pair",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": answer},
                            "finish_reason": None,
                        }
                    ],
                }
                # The page appends deltas. This flag drops a prefix that
                # streamed before the reply turned harmful.
                if flushed:
                    refused["pi_replace"] = True
                if not safe_write(
                    self, f"data: {json.dumps(refused)}\n\n".encode(), flush=True
                ):
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    remember_completion(prompt, answer, chip, peer["name"])
                    return
            else:
                answer = "".join(parts)
            if not policy and not is_harmful(prompt):
                answer = scrub_reply(answer) or answer
                streamed = answer
                more = self._one_more_round(
                    peer,
                    kind,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    prompt,
                    search_note,
                    answer,
                )
                cut = (getattr(self, "_usage", {}) or {}).get("done_reason") == "length"
                more = mend_cut_tail(more, cut)
                shown = clean_reply(
                    more,
                    prompt,
                    _source_count(search_note),
                    context=getattr(self, "_local_context", ""),
                    have_tools=_searched(search_note),
                )
                visible = "\n".join(
                    line for line in streamed.splitlines() if line.strip()
                ).strip()
                if shown != visible:
                    extra = {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": shown},
                                "finish_reason": None,
                            }
                        ],
                        "pi_replace": True,
                    }
                    safe_write(
                        self, f"data: {json.dumps(extra)}\n\n".encode(), flush=True
                    )
                answer = shown
            trainable = not policy
            if not policy and not str(answer).strip():
                err = {"error": friendly_error("")}
                safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
                safe_write(self, b"data: [DONE]\n\n", flush=True)
                return
            elapsed = int((time.time() - started) * 1000)
            final = {
                "id": "pi-pair",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
                "pi_peer": peer["name"],
                "pi_chip": "brain: pi4" if peer["name"] == "pi4" else peer["name"],
                "pi_ms": elapsed,
                "pi_model": used,
                "pi_kind": kind,
            }
            if think_name:
                final["pi_think"] = think_name
            if search_note:
                final["pi_search"] = search_note["status"]
                final["pi_sources"] = search_note["sources"]
            final["pi_stages"] = list(stages)
            self._attach_timing(final, started)
            final.update(note)
            chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
            if not policy:
                raw_answer = "".join(parts)
                if not held and answer == raw_answer:
                    answer = (
                        scrub_reply(raw_answer) or raw_answer
                        if not is_harmful(prompt)
                        else raw_answer
                    )
                elif not held and not is_harmful(prompt):
                    answer = scrub_reply(answer) or answer
                refused = _block(answer)
                if refused:
                    answer = refused
                note_exchange(
                    prompt, answer, chip=chip, peer=peer["name"], train=trainable
                )
            remember_completion(prompt, answer, chip, peer["name"])
            remembered = getattr(self, "_memory_rows", None)
            self._touch_memory(messages if remembered is None else remembered)
            apply_tier(self, final)
            safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except (OSError, json.JSONDecodeError, DecodeFailed) as error:
            shown = (
                str(error) if isinstance(error, DecodeFailed) else public_failure(error)
            )
            err = {"error": shown}
            safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except ClientGone:
            return
        except Exception as error:
            err = {"error": public_failure(error, search_note)}
            apply_tier(self, err)
            safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _emit_ready_answer(
        self,
        peer,
        kind,
        used,
        prompt: str,
        answer: str,
        started: float,
        think_name: str,
        search_note: dict | None,
        stages: list[str],
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
    ) -> None:
        """Send a finished answer that was taken from the pages, not the model."""
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        chunk = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"content": answer}, "finish_reason": None}
            ],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_model": used,
            "pi_kind": kind,
        }
        chunk.update(mode_fields(mode_name, route_name, resident_name))
        apply_tier(self, chunk)
        if not safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True):
            return
        note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
        remember_completion(prompt, answer, chip, peer["name"])
        elapsed = int((time.time() - started) * 1000)
        final = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_ms": elapsed,
            "pi_model": used,
            "pi_kind": kind,
            "pi_stages": list(stages),
        }
        if think_name:
            final["pi_think"] = think_name
        if search_note:
            final["pi_search"] = search_note["status"]
            final["pi_sources"] = search_note["sources"]
        self._attach_timing(final, started)
        final.update(mode_fields(mode_name, route_name, resident_name))
        apply_tier(self, final)
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _complete(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        started,
        prompt: str,
        think_name: str = "",
        search_note: dict | None = None,
        stages: list[str] | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
        ready_answer: str | None = None,
        memory_turns=None,
    ) -> None:
        grounded = ready_answer
        train = True
        if grounded is not None:
            content = grounded
            used = model
        else:
            try:
                content, used, train = self._decode_reply(
                    peer,
                    kind,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    prompt,
                    search_note,
                )
            except DecodeFailed as error:
                self._error(str(error))
                return
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        if train:
            note_exchange(prompt, content, chip=chip, peer=peer["name"], train=True)
        remember_completion(prompt, content, chip, peer["name"])
        self._touch_memory(messages if memory_turns is None else memory_turns)
        elapsed = int((time.time() - started) * 1000)
        message = {"role": "assistant", "content": content}
        reasoning = getattr(self, "_last_reasoning", "") or ""
        if grounded is None and reasoning and not _block(reasoning):
            message["reasoning_content"] = reasoning
        resp = {
            "id": "pi-pair",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_ms": elapsed,
            "pi_model": used,
            "pi_kind": kind,
        }
        self._attach_timing(resp, started)
        if think_name:
            resp["pi_think"] = think_name
        if search_note:
            resp["pi_search"] = search_note["status"]
            resp["pi_sources"] = search_note["sources"]
        if stages:
            resp["pi_stages"] = stages
        resp.update(mode_fields(mode_name, route_name, resident_name))
        apply_tier(self, resp)
        body = json.dumps(resp).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header("X-Pi-Chip", chip)
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, resident_name)
        if search_note:
            self.send_header("X-Pi-Search", search_note["status"])
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)
