/** Health polls and dropped chats stay quiet when a tab is in the background. */

export const RESUME_GRACE_MS = 2500;
export const VISIBILITY_SETTLE_MS = 600;
export const HEALTH_POLL_MS = 8000;
/** Total tries, including the first, before "Connection dropped". */
export const SOFT_ATTEMPTS = 3;

const SOFT_DELAYS_MS = [400, 900];

export interface ServiceHealth {
  ok?: boolean;
  latency_ms?: number | null;
}

export interface HealthSnapshot {
  peers?: unknown[];
  peers_up?: number;
  uptime_s?: number;
  services?: {
    brain?: ServiceHealth;
    search?: ServiceHealth;
    peers_up?: number;
    peers?: number;
  };
}

export interface ServiceView {
  now: string;
  signature: string;
  event: string;
}

export function shouldPollHealth(pageHidden: boolean): boolean {
  return !pageHidden;
}

export function suppressOfflineBanner(
  pageHidden: boolean,
  resumedAt: number,
  now: number,
  grace = RESUME_GRACE_MS,
  streaming = false,
): boolean {
  if (pageHidden || streaming) return true;
  return resumedAt > 0 && now - resumedAt < grace;
}

/** `attempt` is 0-based. True while another try should run before the drop message. */
export function shouldSoftRetry(attempt: number, limit = SOFT_ATTEMPTS): boolean {
  return attempt + 1 < limit;
}

export function softRetryDelay(attempt: number): number {
  const index = Math.max(0, Math.min(attempt, SOFT_DELAYS_MS.length - 1));
  return SOFT_DELAYS_MS[index];
}

export function formatUptime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return hours + "h " + minutes + "m";
  if (minutes) return minutes + "m " + (total % 60) + "s";
  return total + "s";
}

function latency(ms: number | null | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return "";
  return " " + Math.round(ms) + "ms";
}

function mark(name: string, row: ServiceHealth | undefined): string {
  return name + " " + (row?.ok ? "up" : "down") + latency(row?.latency_ms);
}

/** Live uptime line plus a stable signature so the log ignores clock ticks. */
export function serviceView(body: HealthSnapshot): ServiceView {
  const services = body.services || {};
  const up = services.peers_up ?? body.peers_up ?? 0;
  const total = services.peers ?? (body.peers || []).length;
  const bits = [
    mark("Chat", services.brain),
    mark("Search", services.search),
    "Fleet " + up + "/" + total,
  ];
  return {
    now: "Up " + formatUptime(body.uptime_s || 0) + " · " + bits.join(" · "),
    signature: bits.join("|"),
    event: bits.join(" · "),
  };
}
