#!/usr/bin/env python3
"""One chat turn, then a thumbs vote, through the same router the page uses.

Stdlib only. Auto and mesh stay on, so a miss is still generated on pi4.
A bot is another caller of that HTTP API. --dry-run prints the posts and
does not open a connection.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:18080"
DEFAULT_MODEL = "qwen2.5:0.5b"
DEFAULT_MODE = "flash"


def chat_body(prompt: str, think: str, model: str, mode: str) -> dict:
    return {
        "model": model,
        "mode": mode,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": think,
        "pi_target": "auto",
        "pi_mesh": "on",
    }


def feedback_body(prompt: str, answer: str, vote: str, correction: str) -> dict:
    body = {"vote": vote, "prompt": prompt, "answer": answer}
    if correction:
        body["correction"] = correction
    return body


def plan(
    base: str,
    prompt: str,
    vote: str,
    correction: str,
    think: str,
    model: str,
    mode: str,
) -> dict:
    root = base.rstrip("/")
    return {
        "dry_run": True,
        "chat": {
            "url": root + "/v1/chat/completions",
            "headers": {"X-Pi-Target": "auto", "X-Pi-Mesh": "on", "X-Pi-Mode": mode},
            "body": chat_body(prompt, think, model, mode),
        },
        "feedback": {
            "url": root + "/v1/flywheel/feedback",
            "body": feedback_body(prompt, "<answer>", vote, correction),
        },
    }


def _post(url: str, payload: dict, timeout: float, mode: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "X-Pi-Target": "auto",
            "X-Pi-Mesh": "on",
            "X-Pi-Mode": mode,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read().decode() or ""
        try:
            detail = json.loads(raw).get("error") or raw
        except json.JSONDecodeError:
            detail = raw or error.reason
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error


def answer_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "").strip()


def run(
    base: str,
    prompt: str,
    vote: str,
    correction: str,
    think: str,
    model: str,
    mode: str,
    timeout: float,
) -> dict:
    root = base.rstrip("/")
    chat = _post(
        root + "/v1/chat/completions",
        chat_body(prompt, think, model, mode),
        timeout,
        mode,
    )
    answer = answer_text(chat)
    if not answer:
        raise RuntimeError("chat returned no answer")
    labeled = _post(
        root + "/v1/flywheel/feedback",
        feedback_body(prompt, answer, vote, correction),
        min(timeout, 30),
        mode,
    )
    return labeled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post one chat turn and a label.")
    parser.add_argument(
        "--base", default=DEFAULT_BASE, help="Router origin, port 18080"
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--vote", required=True, choices=("up", "down"))
    parser.add_argument("--correction", default="")
    parser.add_argument("--think", default="medium", choices=("low", "medium", "high"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=("flash", "pro"))
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the posts and do not connect"
    )
    args = parser.parse_args(argv)
    prompt = args.prompt.strip()
    correction = args.correction.strip()
    if not prompt:
        print("prompt is empty", file=sys.stderr)
        return 2
    if not args.base.strip():
        print("base is empty", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            json.dumps(
                plan(
                    args.base,
                    prompt,
                    args.vote,
                    correction,
                    args.think,
                    args.model,
                    args.mode,
                )
            )
        )
        return 0
    try:
        print(
            json.dumps(
                run(
                    args.base,
                    prompt,
                    args.vote,
                    correction,
                    args.think,
                    args.model,
                    args.mode,
                    args.timeout,
                )
            )
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
