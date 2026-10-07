import { browserStorage } from "./settings";
import { CLIENT_KEY, ui } from "./state";

export function freshId(): string {
  const raw = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `c${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
  return raw.replace(/[^A-Za-z0-9._-]/g, "").slice(0, 80);
}

export function storedId(kind: "local" | "session", key: string): string {
  const store = browserStorage(kind);
  const existing = store?.getItem(key) || "";
  if (/^[A-Za-z0-9._-]{8,80}$/.test(existing)) return existing;
  const made = freshId();
  store?.setItem(key, made);
  return made;
}

export function sessionHeaders(): Record<string, string> {
  return {
    "X-Pi-Client": storedId("local", CLIENT_KEY),
    "X-Pi-Chat": ui.chatId,
  };
}
