import { WAITING_LINE, friendlyError } from "../core/errors";
import { ui } from "../core/state";
import type { SearchInfo, SourceLink, StageName } from "../core/types";

export function modeModel(): string {
  return ui.modelMode === "pro" ? "pro" : "flash";
}

export function withoutThinkTags(text: string): string {
  return String(text || "")
    .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, "")
    .replace(/<think(?:ing)?>[\s\S]*$/gi, "")
    .replace(/<\/think(?:ing)?>/gi, "");
}

export function thoughtSummary(seconds: number, live: boolean): string {
  if (live) return "Thinking…";
  return "Thought for " + Math.max(1, seconds) + "s";
}

export function waitingCopy(queue: { position?: number; eta_s?: number } | null): string {
  const ahead = Number(queue?.position);
  const eta = Number(queue?.eta_s);
  if (!Number.isFinite(ahead) || ahead <= 0 || !Number.isFinite(eta) || eta <= 0) {
    return WAITING_LINE;
  }
  const seconds = Math.max(1, Math.round(eta));
  return "You're #" + Math.round(ahead) + ", about " + seconds + " s";
}

export function stageText(name: StageName, search: SearchInfo | null): string {
  if (name === "loading") return "Thinking";
  if (name === "waiting") return WAITING_LINE;
  if (name === "thinking") return "Thinking";
  if (name === "searching") {
    if (search?.status === "failed") return "Search failed";
    if (search?.status === "ok") return "Searched";
    return "Searching";
  }
  return "Answering";
}

export function shownError(err: unknown): string {
  return friendlyError((err as { message?: string })?.message || err || "");
}

export function visibleReply(text: string): boolean {
  return text.trim().length > 0;
}

export function searchFrom(payload: { pi_search?: string; pi_sources?: SourceLink[] }, fallback: SearchInfo | null): SearchInfo | null {
  if (!payload.pi_search) return fallback;
  return {
    status: payload.pi_search,
    sources: Array.isArray(payload.pi_sources) ? payload.pi_sources : (fallback?.sources || []),
  };
}

export function freshRequestId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") return cryptoObj.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const nibble = Math.floor(Math.random() * 16);
    const value = ch === "x" ? nibble : (nibble & 0x3) | 0x8;
    return value.toString(16);
  });
}

export function imageWordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}
