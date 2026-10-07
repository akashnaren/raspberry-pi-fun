import { chatBody } from "../chat/history";
import { modeModel } from "../chat/text";
import { sessionHeaders } from "../core/session";
import { ui } from "../core/state";
import { RING_C, turns } from "../main";

export function ringDashOffset(used: number, numCtx: number): number {
  const ratio = numCtx > 0 ? used / numCtx : 0;
  const clamped = Math.min(1, Math.max(0, ratio));
  return RING_C * (1 - clamped);
}

export function memoryMessages(): { role: string; content: string }[] {
  const body = chatBody(turns, {
    model: modeModel(),
    effort: ui.thinking || "medium",
    mode: ui.modelMode,
    sys: "",
  });
  return body.messages.filter((row) => row.role === "user" || row.role === "assistant").slice(-64);
}

export function paintRing(usage: { used?: number; num_ctx?: number; compactions?: number }): void {
  const button = document.getElementById("btnMemory");
  if (!button) return;
  const used = Number(usage.used) || 0;
  const numCtx = Number(usage.num_ctx) || 0;
  const ratio = numCtx > 0 ? used / numCtx : 0;
  const fill = button.querySelector(".ring-fill");
  if (fill) fill.setAttribute("stroke-dashoffset", ringDashOffset(used, numCtx).toFixed(2));
  const level = ratio >= ui.compactAt ? "high" : ratio >= ui.compactBusyAt ? "mid" : "low";
  button.setAttribute("data-level", level);
  const pct = Math.round(Math.min(100, Math.max(0, ratio * 100)));
  const label = `Context ${pct}% used. Compact memory.`;
  button.setAttribute("aria-label", label);
  if (!button.classList.contains("compacting") && button.dataset.titleHold !== "1") {
    button.title = label;
  }
  const pop = document.getElementById("memPopText");
  const count = Number(usage.compactions) || 0;
  ui.ringCompactions = count;
  if (pop) {
    const noun = count === 1 ? "compaction" : "compactions";
    pop.textContent = `Context ${pct}% used · ${count} ${noun}`;
  }
}

export function holdRingTitle(text: string, ms: number): void {
  const button = document.getElementById("btnMemory");
  if (!button) return;
  button.dataset.titleHold = "1";
  button.title = text;
  window.clearTimeout(ui.ringTitleTimer);
  ui.ringTitleTimer = window.setTimeout(() => {
    button.dataset.titleHold = "";
    void refreshMemoryRing();
  }, ms);
}

export async function refreshMemoryRing(): Promise<{ compactions: number } | null> {
  try {
    const response = await fetch("/v1/memory", { headers: sessionHeaders() });
    if (!response.ok) return null;
    const body = await response.json();
    if (typeof body.compact_at === "number") ui.compactAt = body.compact_at;
    if (typeof body.compact_busy_at === "number") ui.compactBusyAt = body.compact_busy_at;
    const usage = body.usage && typeof body.usage === "object" ? body.usage : {};
    paintRing(usage);
    return { compactions: Number(usage.compactions) || 0 };
  } catch {
    return null;
  }
}

export function showMemPop(): void {
  const pop = document.getElementById("memPop");
  if (!pop) return;
  window.clearTimeout(ui.memHideTimer);
  window.clearTimeout(ui.memShowTimer);
  ui.memShowTimer = window.setTimeout(() => {
    pop.hidden = false;
  }, 150);
}

export function hideMemPopSoon(): void {
  const pop = document.getElementById("memPop");
  if (!pop) return;
  window.clearTimeout(ui.memShowTimer);
  window.clearTimeout(ui.memHideTimer);
  ui.memHideTimer = window.setTimeout(() => {
    pop.hidden = true;
  }, 200);
}

export async function pollCompaction(before: number): Promise<void> {
  const usage = await refreshMemoryRing();
  if (!usage || usage.compactions <= before) return;
  const button = document.getElementById("btnMemory");
  if (!button) return;
  button.classList.remove("pulsed");
  void button.offsetWidth;
  button.classList.add("pulsed");
  holdRingTitle("Compacted", 2000);
}

export async function compactMemory(): Promise<void> {
  const button = document.getElementById("btnMemory");
  if (!button || button.classList.contains("compacting")) return;
  const before = ui.ringCompactions;
  button.classList.add("compacting");
  try {
    const response = await fetch("/v1/memory/compact", {
      method: "POST",
      headers: { "content-type": "application/json", ...sessionHeaders() },
      body: JSON.stringify({ messages: memoryMessages() }),
    });
    if (response.status === 409) {
      holdRingTitle("Busy, try after this reply", 2000);
      return;
    }
    if (!response.ok) return;
    const body = await response.json();
    if (body && body.ok === true && body.queued === true) {
      window.setTimeout(() => void pollCompaction(before), 2000);
      window.setTimeout(() => void pollCompaction(before), 6000);
    }
  } catch {
    return;
  } finally {
    button.classList.remove("compacting");
  }
}
