from __future__ import annotations

import json
import os
import threading
import time

from pair.core import runtime
from pair.core.cancel import Cancel, ClientGone, peer_closed
from pair.core.errors import ASK_FIRST, BUSY, FLASH_WARMING, MODEL_MISSING
from pair.core.timing import assemble, present
from pair.flywheel.miss_queue import node_role
from pair.ingest.docfit import fit_outbound
from pair.mesh.guard import may_generate, weak_brain_error
from pair.mesh.peers import pick
from pair.model.chat_once import llamacpp_model
from pair.model.knobs import decode_effort, inference_knobs, mode_limits
from pair.model.modes import (
    mode_table,
    pull_needed,
    resolve_auto,
    resolve_mode,
    tag_ready,
)
from pair.model.preload import resident_models, schedule_pro_warm, wait_for_resident
from pair.model.think import decode_plan
from pair.routes.base import safe_write, status_event, write_event
from pair.routes.memory import _memory_prompt
from pair.routes.public_api import stamp
from pair.routes.search import _begin_lookup
from pair.routes.status import _claim_wait, listed_chat_models, mode_fields
from pair.turn.abilities import tail_hints
from pair.turn.moderate import moderate
from pair.turn.shape import (
    is_structured_request,
    needs_web,
    shape_messages,
    structure_hint,
    turns_for_memory,
)

_CHAT_ROLES = {"system", "user", "assistant"}


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return str(content or "")


def _named_can_generate(target: str) -> bool:
    named = next((peer for peer in runtime.PEERS if peer["name"] == target), None)
    return named is not None and may_generate(named)


def one_user_turn(messages) -> bool:
    """The canned map is for a single new line. A session with history stays on pi4."""
    users = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        if role == "assistant":
            return False
        if role == "user":
            users += 1
            if users > 1:
                return False
    return True


def last_user_text(messages) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if message.get("role", "user") == "user":
            return message_text(message.get("content"))
    return ""


def _block(text: str) -> str:
    """Replacement from `moderate`, or empty when that span is allowed."""
    verdict = moderate(text or "")
    return verdict.replacement if verdict.refused else ""


def _tuned_knobs(model: str) -> dict:
    """File knobs with this mode's context, threads, and batch."""
    row = inference_knobs()
    tuned = dict(row)
    tuned.update(mode_limits(str(model or ""), row))
    return tuned


def _prompt_note(search_note) -> str:
    if not isinstance(search_note, dict):
        return ""
    return str(search_note.get("prompt_note") or "")


def _apply_model_sample(handler, model: str, temperature: float, max_tokens: int):
    """Flash and Pro use different temperatures once the tag is final."""
    plan = getattr(handler, "_decode_plan", None)
    if plan is None:
        return temperature, max_tokens
    table = mode_table()
    pro_tag = str(table.get("pro") or "")
    flash_tag = str(table.get("flash") or "")
    name = str(model or "")
    pro = bool(pro_tag) and name == pro_tag and name != flash_tag
    tuned = decode_plan(getattr(plan, "name", ""), pro=pro)
    if tuned is None:
        return temperature, max_tokens
    handler._decode_plan = tuned
    return tuned.temperature, tuned.num_predict


def apply_tier(handler, payload: dict) -> dict:
    """Keyed API keeps its public mode stamp. The LAN page adds pi_mode only."""
    public = getattr(handler, "public_mode", "") or ""
    stamp(payload, public)
    tier = getattr(handler, "pi_mode", "") or ""
    if tier and not public and isinstance(payload, dict):
        payload["pi_mode"] = tier
    return payload


def _recall_context(handler, messages, prompt: str) -> str:
    """Facts, the summary, and earlier turns. The current question is not a source."""
    facts, summary = _memory_prompt(handler._memory_scope())
    question = " ".join((prompt or "").split())
    parts = [facts, summary]
    for row in messages or []:
        if not isinstance(row, dict) or row.get("role") == "system":
            continue
        text = " ".join(str(row.get("content") or "").split())
        if not text or text == question:
            continue
        parts.append(text)
    return "\n".join(part for part in parts if part)


class ChatRoutes:
    def _validate_chat(self, data: dict) -> bool:
        """False after a 400. A chat needs one non-empty user message."""
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            self._error(ASK_FIRST, status=400)
            return False
        for message in messages:
            if not isinstance(message, dict):
                self._error(ASK_FIRST, status=400)
                return False
            role = message.get("role", "user")
            if role not in _CHAT_ROLES:
                self._error(ASK_FIRST, status=400)
                return False
            content = message.get("content")
            if not isinstance(content, (str, list)):
                self._error(ASK_FIRST, status=400)
                return False
        if not last_user_text(messages).strip():
            self._error(ASK_FIRST, status=400)
            return False
        return True

    def _reset_timing(self) -> None:
        self._queue_ms = 0
        self._search_ms = 0
        self._usage = {}
        self._ttft_ms = 0
        self._queue_t0 = None

    def _mark_queue(self) -> None:
        started = getattr(self, "_queue_t0", None)
        if started is None:
            return
        self._queue_ms = int((time.perf_counter() - started) * 1000)

    def _attach_timing(self, payload: dict, started: float) -> None:
        timing = assemble(
            queue_ms=getattr(self, "_queue_ms", 0),
            search_ms=getattr(self, "_search_ms", 0),
            usage=getattr(self, "_usage", None),
            total_ms=int((time.time() - started) * 1000),
            ttft_ms=getattr(self, "_ttft_ms", 0),
        )
        payload["pi_timing"] = present(timing, self._public())

    def _effort_name(self) -> str:
        """Low, Medium, or High when the turn named one. Empty otherwise."""
        plan = getattr(self, "_decode_plan", None)
        return str(getattr(plan, "name", "") or "")

    def _chosen_mode(self, data: dict) -> str:
        """LAN mode word. Thinking levels are not model choices."""
        if getattr(self, "public_mode", ""):
            return ""
        chosen = (self.headers.get("X-Pi-Mode") or "").strip()
        if not chosen:
            body_mode = data.get("mode", None)
            if body_mode is None:
                body_mode = data.get("pi_mode", None)
            chosen = "" if body_mode is None else str(body_mode)
        picked = chosen.strip().lower()
        if picked in {"low", "medium", "high"}:
            return ""
        return picked

    def _bind_tier(self, data: dict, prompt: str, think_name: str = "") -> str:
        """LAN flash/pro/auto. The keyed API already pinned the Flash checkpoint.

        Empty and unknown modes clamp to the Flash or Pro tag from mode_table.
        Auto uses that same table. Nothing here unloads a resident tag.
        """
        picked = self._chosen_mode(data)
        data.pop("mode", None)
        data.pop("pi_mode", None)
        if getattr(self, "public_mode", ""):
            self.pi_mode = ""
            self.pi_route = ""
            return str(data.get("model") or runtime.MODEL)
        if picked == "auto":
            route, model, _reason = resolve_auto(prompt, listed_chat_models())
            # High is a longer Flash answer. Stay on Flash unless Pro was chosen.
            if think_name == "high":
                route, model = "flash", mode_table()["flash"]
            self.pi_mode = "auto"
            self.pi_route = route
            return model
        mode_name, model = resolve_mode(picked, data.get("model") or runtime.MODEL)
        self.pi_mode = mode_name
        self.pi_route = mode_name
        return model

    def _remember_canned_mode(self, data: dict) -> tuple[str, str]:
        """Name the page mode on a map hit without asking Ollama which tags exist."""
        picked = self._chosen_mode(data)
        data.pop("mode", None)
        data.pop("pi_mode", None)
        if getattr(self, "public_mode", ""):
            self.pi_mode = ""
            self.pi_route = ""
            return "", ""
        if picked == "auto":
            self.pi_mode = "auto"
            self.pi_route = "canned"
            return "auto", "canned"
        mode_name, _model = resolve_mode(picked, data.get("model") or runtime.MODEL)
        self.pi_mode = mode_name
        self.pi_route = "canned"
        return mode_name, "canned"

    def _begin_cancel(self) -> None:
        """Watch the client socket, and replace any earlier turn with this id."""
        self._cancel = Cancel()
        self._watch_stop = threading.Event()
        request_id = (self.headers.get("X-Pi-Request-Id") or "").strip()
        self._request_id = request_id
        if request_id:
            previous = runtime.requests.get(request_id)
            if previous is not None and previous is not self._cancel:
                previous.set()
            runtime.requests[request_id] = self._cancel

        def watch() -> None:
            while not self._watch_stop.wait(1.0):
                try:
                    gone = peer_closed(self.connection)
                except Exception:
                    gone = True
                if gone:
                    self._cancel.set()
                    return

        threading.Thread(target=watch, name="client-watch", daemon=True).start()

    def _end_cancel(self) -> None:
        stop = getattr(self, "_watch_stop", None)
        if stop is not None:
            stop.set()
        request_id = getattr(self, "_request_id", "")
        cancel = getattr(self, "_cancel", None)
        if request_id and cancel is not None:
            if runtime.requests.get(request_id) is cancel:
                runtime.requests.pop(request_id, None)

    def _serve_chat(self, data: dict, raw: bytes) -> None:
        self._begin_cancel()
        try:
            self._serve_chat_body(data, raw)
        except ClientGone:
            return
        finally:
            self._end_cancel()

    def _serve_chat_body(self, data: dict, raw: bytes) -> None:  # noqa: C901
        if not isinstance(data, dict):
            self._error("chat body must be an object", status=400)
            return
        target = (
            self.headers.get("X-Pi-Target") or data.pop("pi_target", None) or "auto"
        ).strip()
        mesh = (
            self.headers.get("X-Pi-Mesh") or data.pop("pi_mesh", None) or "on"
        ).strip().lower() not in (
            "0",
            "off",
            "false",
            "no",
        )
        messages = data.get("messages") or []
        prompt = last_user_text(messages)
        effort = decode_effort(str(data.pop("think", "") or ""), prompt)
        self._decode_plan = effort
        self._last_reasoning = ""
        if effort:
            think_name = effort.name
            temperature = effort.temperature
            max_tokens = effort.num_predict
            data.pop("temperature", None)
            data.pop("max_tokens", None)
            data.pop("max_completion_tokens", None)
        else:
            think_name = ""
            temperature = float(
                data.get("temperature") if data.get("temperature") is not None else 0.7
            )
            max_tokens = int(
                data.get("max_tokens") or data.get("max_completion_tokens") or 256
            )
        started = time.time()
        want_stream = bool(data.get("stream"))
        self._searched = False
        self._reset_timing()
        refused = _block(prompt)
        if refused:
            self._policy_refusal(prompt, want_stream, started, refused)
            return
        try:
            if target and target != "auto":
                named = next(
                    (peer for peer in runtime.PEERS if peer["name"] == target), None
                )
                if named is not None and not may_generate(named):
                    raise RuntimeError(weak_brain_error(named["name"]))
        except ClientGone:
            raise
        except Exception as error:
            self._error(str(error))
            return
        if node_role() != "brain":
            self._relay_to_brain(raw)
            return
        # Pro must already be on disk. A missing tag is not a pull, and it does
        # not take a generation slot. Auto names a mode_table tag and does not
        # plan a swap.
        try:
            model = self._bind_tier(data, prompt, think_name)
            mode_name = getattr(self, "pi_mode", "") or ""
            route_name = getattr(self, "pi_route", "") or ""
            resident_name = ""
            peer = pick(target, mesh, model)
            outbound = [
                {
                    "role": message.get("role", "user"),
                    "content": message_text(message.get("content", "")),
                }
                for message in messages
                if isinstance(message, dict)
            ]
            kind = peer.get("kind") or "ollama"
            used = llamacpp_model(peer, model) if kind == "llamacpp" else model
            structured = is_structured_request(prompt)
            self._local_context = _recall_context(self, outbound, prompt)
            do_search = (
                bool(mesh and node_role() == "brain")
                and not structured
                and needs_web(
                    prompt,
                    follow_up=not one_user_turn(messages),
                    context=self._local_context,
                )
            )
            tuned = _tuned_knobs(used if kind != "llamacpp" else model)
            ctx = int(tuned.get("num_ctx") or 2048)
            outbound = fit_outbound(outbound, num_ctx=ctx, reply_tokens=max_tokens)
            hint = tail_hints(prompt, structure_hint(prompt) or "")
            search_note = None
            search_job = _begin_lookup(prompt, model) if do_search else None
            if do_search and os.environ.get("PI_PAIR_PREFIX_PRIME") == "1":
                from pair.mesh.tools import schedule_prefix_prime

                schedule_prefix_prime(outbound)
            grounded = None
        except ClientGone:
            raise
        except Exception as error:
            self._error(str(error))
            return
        if kind != "llamacpp":
            if route_name == "pro" and not tag_ready(
                peer.get("models") or [], "pro", model
            ):
                self._error(pull_needed(model))
                return
            host = str(peer.get("host") or "127.0.0.1")
            try:
                peer_port = int(peer.get("port") or 0)
            except (TypeError, ValueError):
                peer_port = 0
            resident = resident_models(host, peer_port) if peer_port else None
            # A chat against a tag /api/ps does not list would cold-load it.
            # Warm that tag in the background. Use Flash only when it is already resident.
            if resident is not None and model not in resident:
                schedule_pro_warm(host, peer_port, model)
                flash_tag = str(mode_table().get("flash") or "")
                if flash_tag and flash_tag in resident and model != flash_tag:
                    model = flash_tag
                    used = model
                    route_name = "flash"
                    self.pi_route = "flash"
                elif route_name != "pro":
                    if not wait_for_resident(host, peer_port, model):
                        self._warming(want_stream, FLASH_WARMING, started)
                        return
                else:
                    self._error(MODEL_MISSING)
                    return
        temperature, max_tokens = _apply_model_sample(
            self, model, temperature, max_tokens
        )
        self._answer_cap = int(max_tokens or 0)
        use_model = grounded is None
        slot = {"held": False, "waiting": False}
        if use_model:
            self._queue_t0 = time.perf_counter()
            outcome = runtime.gate.reserve(self._client_key())
            if outcome == "ready":
                self._mark_queue()
            if outcome == "full":
                self._error(
                    BUSY,
                    status=503,
                    headers={"Retry-After": str(runtime.gate.retry_after_s())},
                )
                return
            if outcome == "ready":
                slot["held"] = True
            else:
                slot["waiting"] = True
                from pair.model.sched import overlap, prepare_prefix

                overlap(lambda: prepare_prefix(outbound))
        try:
            if want_stream:
                self._stream(
                    peer,
                    kind,
                    model,
                    used,
                    outbound,
                    temperature,
                    max_tokens,
                    started,
                    prompt,
                    think_name,
                    do_search,
                    searched=False,
                    search_note=None,
                    mode_name=mode_name,
                    route_name=route_name,
                    resident_name=resident_name,
                    ready_answer=grounded,
                    slot=slot,
                    search_job=search_job,
                )
            else:
                if slot["waiting"] and not _claim_wait(
                    slot, cancel=getattr(self, "_cancel", None)
                ):
                    self._error(
                        BUSY,
                        status=503,
                        headers={"Retry-After": str(runtime.gate.retry_after_s())},
                    )
                    return
                if slot["held"]:
                    self._mark_queue()
                if do_search:
                    self._searched = True
                    outbound, search_note = self._timed_search(
                        outbound,
                        prompt,
                        model,
                        getattr(self, "_cancel", None),
                        job=search_job,
                    )
                fact_text, summary_text = _memory_prompt(self._memory_scope())
                source_rows = list(outbound)
                outbound = shape_messages(
                    outbound,
                    prompt,
                    tuned,
                    think_name,
                    _prompt_note(search_note),
                    hints=hint,
                    facts=fact_text,
                    summary=summary_text,
                )
                memory_rows = turns_for_memory(source_rows, outbound)
                stages = ["thinking"]
                if do_search:
                    stages.append("searching")
                stages.append("answering")
                self._complete(
                    peer,
                    kind,
                    model,
                    outbound,
                    temperature,
                    max_tokens,
                    started,
                    prompt,
                    think_name,
                    search_note,
                    stages,
                    mode_name,
                    route_name,
                    resident_name,
                    ready_answer=grounded,
                    memory_turns=memory_rows,
                )
        except ClientGone:
            raise
        except Exception as error:
            self._error(str(error))
        finally:
            self._observe_rates()
            if slot["held"]:
                runtime.gate.release()
            elif slot["waiting"]:
                runtime.gate.cancel_wait()

    def _cached(
        self,
        answer: str,
        want_stream: bool,
        started: float,
        think_name: str = "",
        mode_name: str = "",
        route_name: str = "",
    ) -> None:
        elapsed = int((time.time() - started) * 1000)
        note = mode_fields(mode_name, route_name, "")
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
                "pi_peer": "cache",
                "pi_chip": "cache",
                "pi_ms": elapsed,
                "pi_model": "canned",
                "pi_kind": "dataset",
            }
            if think_name:
                resp["pi_think"] = think_name
            resp["pi_stages"] = ["answering"]
            resp.update(note)
            apply_tier(self, resp)
            body = json.dumps(resp).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("X-Pi-Peer", "cache")
            self.send_header("X-Pi-Chip", "cache")
            if think_name:
                self.send_header("X-Pi-Think", think_name)
            self._write_mode_headers(mode_name, route_name, "")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", "cache")
        self.send_header("X-Pi-Chip", "cache")
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, "")
        self.end_headers()
        answering = {"pi_stages": ["answering"]}
        if think_name:
            answering["pi_think"] = think_name
        answering.update(note)
        write_event(self, status_event("answering", answering))
        first = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }
            ],
            "pi_peer": "cache",
            "pi_chip": "cache",
            "pi_model": "canned",
            "pi_kind": "dataset",
            "pi_stages": ["answering"],
        }
        if think_name:
            first["pi_think"] = think_name
        first.update(note)
        apply_tier(self, first)
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        final = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "pi_peer": "cache",
            "pi_chip": "cache",
            "pi_ms": elapsed,
            "pi_model": "canned",
            "pi_kind": "dataset",
        }
        if think_name:
            final["pi_think"] = think_name
        final["pi_stages"] = ["answering"]
        final.update(note)
        apply_tier(self, final)
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)
