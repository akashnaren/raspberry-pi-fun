import { syncSend } from "../chat/send";
import { byId } from "../core/dom";
import { BIG_LINE, DROP_LINE, OCR_BUSY_LINE, SLOW_LINE, friendlyError } from "../core/errors";
import { ATTACH_BYTES, ui } from "../core/state";
import type { AttachmentResult } from "../core/types";
import { ATTACH_TIMEOUT_MS } from "../main";
import { voiceNote } from "../voice/mode";
import { docExcerpt } from "./message";

export function clearAttach(): void {
  const box = byId<HTMLTextAreaElement>("q");
  delete box.dataset.attachText;
  ui.pendingDoc = null;
  ui.attachError = false;
  byId("fileTag").classList.remove("on", "err");
  byId<HTMLInputElement>("attach").value = "";
  syncSend();
}

export function cancelAttach(): void {
  ui.attachSerial += 1;
  ui.attachXhr?.abort();
  ui.attachXhr = null;
  ui.uploading = false;
  releaseAttachButton();
  clearAttach();
}

export function releaseAttachButton(): void {
  const button = byId<HTMLButtonElement>("btnAttach");
  button.classList.remove("live");
  button.disabled = false;
  button.removeAttribute("aria-busy");
  setReadBar(false);
}

export function setReadBar(on: boolean): void {
  const bar = document.getElementById("readBar");
  if (!bar) return;
  bar.classList.toggle("on", on);
  bar.hidden = !on;
}

export function shortLine(line: string): string {
  if (line === OCR_BUSY_LINE) return "busy";
  if (line === SLOW_LINE) return "too slow";
  if (line === DROP_LINE) return "dropped";
  return "couldn't read it";
}

export function showUploadChip(label: string, failed = false): void {
  byId("fileName").textContent = label;
  const tag = byId("fileTag");
  tag.classList.add("on");
  tag.classList.toggle("err", failed);
  ui.attachError = failed;
}

export async function loadFile(file: File | null): Promise<void> {
  if (!file) return;
  const serial = ++ui.attachSerial;
  const button = byId<HTMLButtonElement>("btnAttach");
  const current = () => serial === ui.attachSerial;
  clearAttach();
  if (!current()) return;
  if (file.size <= 0) {
    voiceNote("That file is empty.");
    releaseAttachButton();
    return;
  }
  if (file.size > ATTACH_BYTES) {
    voiceNote(BIG_LINE);
    releaseAttachButton();
    return;
  }
  button.classList.add("live");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  ui.uploading = true;
  syncSend();
  const label = file.name || "attachment";
  showUploadChip(label + " · 0%");
  voiceNote("");
  const body = new FormData();
  body.append("file", file, label);
  try {
    const response = await new Promise<{ ok: boolean; status: number; text: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      ui.attachXhr = xhr;
      xhr.open("POST", "/v1/attachments");
      xhr.timeout = ATTACH_TIMEOUT_MS;
      xhr.upload.onprogress = (event) => {
        if (!current() || !event.lengthComputable || event.total <= 0) return;
        const pct = Math.min(100, Math.round((100 * event.loaded) / event.total));
        showUploadChip(label + " · " + pct + "%");
      };
      xhr.upload.onload = () => {
        if (!current()) return;
        showUploadChip(label + " · reading…");
        setReadBar(true);
      };
      xhr.onload = () => {
        resolve({
          ok: xhr.status >= 200 && xhr.status < 300,
          status: xhr.status,
          text: xhr.responseText || "",
        });
      };
      xhr.onerror = () => reject(new Error("network"));
      xhr.onabort = () => reject(new Error("abort"));
      xhr.ontimeout = () => reject(new Error("timeout"));
      xhr.send(body);
    });
    let payload: AttachmentResult = {};
    try {
      payload = response.text ? (JSON.parse(response.text) as AttachmentResult) : {};
    } catch {
      if (current()) {
        showUploadChip(label + " · couldn't read it", true);
        voiceNote("");
        syncSend();
      }
      return;
    }
    if (!current()) return;
    if (!response.ok || !String(payload.text || "").trim()) {
      const line = friendlyError(payload.error || response.status);
      showUploadChip(label + " · " + shortLine(line), true);
      voiceNote(line);
      syncSend();
      return;
    }
    const text = String(payload.text || "").trim();
    voiceNote("");
    byId<HTMLTextAreaElement>("q").dataset.attachText = text;
    ui.pendingDoc = {
      name: label,
      route: payload.route === "ocr" ? "ocr" : "text",
      bytes: file.size,
      excerpt: docExcerpt(text),
    };
    const kb = Math.round((text.length / 1024) * 10) / 10;
    const via = payload.route === "ocr" ? "ocr" : "text";
    const cut = payload.truncated ? " · cut" : "";
    showUploadChip(label + " · " + via + cut + " (" + kb + " KB)");
  } catch (err) {
    if (!current()) return;
    const message = err instanceof Error ? err.message : "";
    if (message === "abort") return;
    if (message === "timeout") {
      showUploadChip(label + " · " + shortLine(SLOW_LINE), true);
      ui.attachError = false;
      voiceNote(SLOW_LINE);
      return;
    }
    if (message === "network") {
      showUploadChip(label + " · " + shortLine(DROP_LINE), true);
      voiceNote(DROP_LINE);
      return;
    }
    showUploadChip(label + " · couldn't read it", true);
    voiceNote("");
  } finally {
    if (current()) {
      ui.uploading = false;
      releaseAttachButton();
      syncSend();
      ui.attachXhr = null;
    }
  }
}

export function abortAttachForNavigation(): void {
  if (ui.uploading) cancelAttach();
}
