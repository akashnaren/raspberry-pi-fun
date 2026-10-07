from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from pair.core import runtime
from pair.core.cancel import peer_closed
from pair.flywheel.miss_queue import node_role
from pair.ingest.upload import (
    UploadRejected,
    file_part,
    ingest,
    read_limited,
    route_hint,
    safe_name,
)
from pair.routes.base import safe_write

# One cap for non-text uploads. ocr.try_acquire() inside the child is
# per-process and always free, so this gate is the real limit.
_ATTACH_GATE = threading.BoundedSemaphore(2)


def _local_extract(
    content_type: str, body: bytes, filename: str, deadline_s: float, gone
) -> dict:
    """In-process fallback when pi3 cannot take the file. Tests patch this."""
    from pair.ingest import job as ingest_job

    return ingest_job.run(content_type, body, filename, deadline_s, gone)


def _offload_extract(
    content_type: str,
    body: bytes,
    filename: str,
    deadline_s: float,
    gone,
) -> dict | None:
    """Send the raw upload to pi3 once. None means the local child should run.

    A connect failure, HTTP 404, or HTTP 5xx falls back. A timeout does not,
    and neither does a definite 413, 415, or 422: the file is not read twice.
    This does not use tools.Dispatcher, whose hedge would OCR on pi2 as well.
    """
    import base64
    import socket

    from pair.ingest import job as ingest_job

    if node_role() != "brain":
        return None
    peer = next((item for item in runtime.PEERS if item.get("name") == "pi3"), None)
    if not peer or not peer.get("host") or not peer.get("port"):
        return None
    url = f"http://{peer['host']}:{int(peer['port'])}/tools/extract"
    raw = json.dumps(
        {
            "filename": filename or "",
            "content_type": content_type or "",
            "data": base64.b64encode(body or b"").decode("ascii"),
        }
    ).encode()
    box: dict = {}

    def _post() -> None:
        request = urllib.request.Request(
            url,
            data=raw,
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=max(0.1, float(deadline_s))
            ) as response:
                box["code"] = int(getattr(response, "status", 200) or 200)
                box["body"] = response.read()
        except urllib.error.HTTPError as exc:
            box["code"] = int(exc.code)
            try:
                box["body"] = exc.read()
            except Exception:
                box["body"] = b""
        except Exception as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(
                reason, (TimeoutError, socket.timeout)
            ):
                box["timeout"] = True
            else:
                box["down"] = True

    thread = threading.Thread(target=_post, daemon=True)
    thread.start()
    limit = time.monotonic() + max(0.0, float(deadline_s))
    while thread.is_alive():
        thread.join(ingest_job.POLL_S)
        if _caller_gone(gone):
            return {"ok": False, "status": 499, "error": "client left"}
        if time.monotonic() >= limit:
            return {
                "ok": False,
                "status": 504,
                "error": "reading that file took too long",
            }
    if box.get("timeout"):
        return {
            "ok": False,
            "status": 504,
            "error": "reading that file took too long",
        }
    if box.get("down"):
        return None
    code = int(box.get("code") or 0)
    if code in {0, 404} or code >= 500:
        return None
    try:
        parsed = json.loads((box.get("body") or b"{}").decode() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or "ok" not in parsed:
        return None
    if not parsed.get("ok") and "status" not in parsed:
        parsed = dict(parsed)
        parsed["status"] = code
    return parsed


def _caller_gone(gone) -> bool:
    try:
        return bool(gone())
    except Exception:
        return True


class FileRoutes:
    def _upload_identity(
        self, content_type: str, raw: bytes, filename: str
    ) -> tuple[str, str]:
        """Name and MIME for the fast path. Multipart is one split, not a parse."""
        if (content_type or "").lower().startswith("multipart/"):
            name, mime, _data = file_part(raw, content_type)
            return name, mime
        return safe_name(filename or "attachment"), content_type or ""

    def _send_attachment(self, result: dict, via: str = "") -> None:
        body = json.dumps(result).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        if via in {"pi3", "local"}:
            self.send_header("X-Pi-Extract", via)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _finish_ingest(self, result: dict, via: str = "") -> None:
        extra = {"X-Pi-Extract": via} if via in {"pi3", "local"} else None
        if not isinstance(result, dict) or not result.get("ok"):
            status = 422
            message = "could not read that file"
            if isinstance(result, dict):
                try:
                    status = int(result.get("status") or 422)
                except (TypeError, ValueError):
                    status = 422
                if result.get("error"):
                    message = str(result.get("error"))
            if status == 499:
                self.close_connection = True
                return
            self._error(message, status=status, headers=extra)
            return
        self._send_attachment(result, via)

    def _attachment(self) -> None:
        """Text stays on this thread. Other files run in a killable child."""
        try:
            try:
                self.connection.settimeout(30)
            except OSError:
                pass
            raw = read_limited(self.headers.get("content-length"), self.rfile.read)
        except UploadRejected as error:
            self._error(str(error), status=error.status)
            return
        finally:
            try:
                self.connection.settimeout(None)
            except OSError:
                pass
        content_type = self.headers.get("content-type") or ""
        filename = self.headers.get("x-filename") or ""
        try:
            name, mime = self._upload_identity(content_type, raw, filename)
        except UploadRejected as error:
            self._error(str(error), status=error.status)
            return
        if route_hint(name, mime, raw[:512]) == "text":
            try:
                result = ingest(content_type, raw, filename=filename)
            except UploadRejected as error:
                self._error(str(error), status=error.status)
                return
            self._send_attachment(result)
            return
        if not _ATTACH_GATE.acquire(blocking=False):
            self._error("OCR is busy", status=429)
            return
        from pair.ingest import job as ingest_job
        from pair.ingest import ocr

        held = ocr.hold_embed()
        try:
            started = time.monotonic()

            def gone() -> bool:
                return peer_closed(self.connection)

            offload = _offload_extract(
                content_type,
                raw,
                filename,
                ingest_job.UPLOAD_DEADLINE_S - 5,
                gone,
            )
            if offload is not None:
                result, via = offload, "pi3"
            else:
                remaining = ingest_job.UPLOAD_DEADLINE_S - (time.monotonic() - started)
                if remaining <= 0:
                    result = {
                        "ok": False,
                        "status": 504,
                        "error": "reading that file took too long",
                    }
                else:
                    result = _local_extract(
                        content_type,
                        raw,
                        filename,
                        remaining,
                        gone,
                    )
                via = "local"
        finally:
            ocr.release_embed(held)
            _ATTACH_GATE.release()
        self._finish_ingest(result, via)

    def _run_extract(self, payload: dict) -> tuple[int, dict]:
        """Same ingest as an upload, so a text PDF is not rasterized first."""
        import base64

        from pair.ingest import job as ingest_job
        from pair.ingest import ocr

        if not _ATTACH_GATE.acquire(blocking=False):
            return 429, {"ok": False, "error": "OCR is busy", "status": 429}
        held = ocr.hold_embed()
        try:
            raw = payload.get("data") or ""
            try:
                blob = base64.b64decode(raw) if isinstance(raw, str) else b""
            except Exception:
                return 422, {
                    "ok": False,
                    "error": "could not read that file",
                    "status": 422,
                }
            result = ingest_job.run(
                str(payload.get("content_type") or ""),
                blob,
                str(payload.get("filename") or ""),
                max(0.1, ingest_job.UPLOAD_DEADLINE_S - 5),
                lambda: peer_closed(self.connection),
            )
        finally:
            ocr.release_embed(held)
            _ATTACH_GATE.release()
        if not isinstance(result, dict):
            return 422, {
                "ok": False,
                "error": "could not read that file",
                "status": 422,
            }
        if result.get("ok"):
            return 200, result
        try:
            status = int(result.get("status") or 422)
        except (TypeError, ValueError):
            status = 422
        return status, result

    def _file_get(self, doc_id: str) -> None:
        from pair.render.documents import open_document

        found = open_document(doc_id.split("/")[0])
        if found is None:
            self._error("file not found", status=404)
            return
        data, name, mime = found
        self.send_response(200)
        self._cors()
        self.send_header("content-type", mime)
        self.send_header("content-disposition", f'attachment; filename="{name}"')
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        safe_write(self, data)
