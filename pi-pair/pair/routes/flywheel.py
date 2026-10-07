from __future__ import annotations

import json

from pair.flywheel.miss_queue import append_row, apply_label, node_role
from pair.routes.base import safe_write
from pair.routes.reply import last_completion


class FlywheelRoutes:
    def _enqueue(self) -> None:
        if node_role() != "dataset":
            self._error("train queue is accepted only on the dataset host", status=403)
            return
        row = self._read_json(label="queue row", empty_ok=True)
        if row is None:
            return
        prompt = str(row.get("prompt") or "").strip()
        answer = str(row.get("answer") or "").strip()
        if not prompt or not answer:
            self._error("queue row needs prompt and answer", status=400)
            return
        kept = append_row(
            {
                "prompt": prompt,
                "answer": answer,
                "chip": row.get("chip") or "",
                "peer": row.get("peer") or "",
            }
        )
        body = json.dumps({"ok": True, "queued": kept}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _feedback(self) -> None:
        row = self._read_json(label="feedback", empty_ok=True)
        if row is None:
            return
        prompt = str(row.get("prompt") or "").strip()
        answer = str(row.get("answer") or "").strip()
        chip = str(row.get("chip") or "")
        peer = str(row.get("peer") or "")
        if not prompt and not answer:
            last = last_completion()
            prompt = str(last.get("prompt") or "")
            answer = str(last.get("answer") or "")
            chip = chip or str(last.get("chip") or "")
            peer = peer or str(last.get("peer") or "")
        elif not prompt or not answer:
            self._error("feedback needs both prompt and answer, or neither", status=400)
            return
        try:
            result = apply_label(
                prompt,
                answer,
                str(row.get("vote") or ""),
                str(row.get("correction") or ""),
                chip=chip,
                peer=peer,
            )
        except ValueError as error:
            self._error(str(error), status=400)
            return
        if result.get("forwarded") and not result.get("ok"):
            self._error("dataset host did not accept the label", status=502)
            return
        stored = result.get("row") if isinstance(result.get("row"), dict) else {}
        body = json.dumps(
            {
                "ok": True,
                "labeled": True,
                "queued": result.get("queued") or 0,
                "prompt": stored.get("prompt") or prompt,
                "answer": stored.get("answer") or answer,
                "vote": stored.get("vote") or "",
                "correction": stored.get("correction") or "",
            }
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)
