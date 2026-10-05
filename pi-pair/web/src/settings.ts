/** Persisted page settings. Dark is the default Temporal theme. */

import { END_OF_UTTERANCE_SILENCE_MS, setEndOfUtteranceSilence } from "./voice";

export const SETTINGS_KEY = "openpi.settings";

export type ThemeName = "dark" | "light";
export type ModelMode = "auto" | "flash" | "pro";
export type ThinkLevel = "low" | "medium" | "high";

export interface PageSettings {
  theme: ThemeName;
  mode: ModelMode;
  thinking: ThinkLevel;
  voiceSilenceMs: number;
  enterToSend: boolean;
}

export function defaultSettings(): PageSettings {
  return {
    theme: "dark",
    mode: "auto",
    thinking: "medium",
    voiceSilenceMs: END_OF_UTTERANCE_SILENCE_MS,
    enterToSend: true,
  };
}

function asTheme(value: unknown): ThemeName {
  return value === "light" ? "light" : "dark";
}

function asMode(value: unknown): ModelMode {
  return value === "flash" || value === "pro" ? value : "auto";
}

function asThink(value: unknown): ThinkLevel {
  return value === "low" || value === "high" ? value : "medium";
}

function asSilence(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return END_OF_UTTERANCE_SILENCE_MS;
  const next = Math.round(n);
  if (next < 0 || next > 10000) return END_OF_UTTERANCE_SILENCE_MS;
  return next;
}

export function loadSettings(storage: Storage | null): PageSettings {
  const base = defaultSettings();
  if (!storage) return base;
  try {
    const raw = storage.getItem(SETTINGS_KEY);
    if (!raw) return base;
    const parsed = JSON.parse(raw) as Partial<PageSettings>;
    return {
      theme: asTheme(parsed.theme),
      mode: asMode(parsed.mode),
      thinking: asThink(parsed.thinking),
      voiceSilenceMs: asSilence(parsed.voiceSilenceMs),
      enterToSend: parsed.enterToSend !== false,
    };
  } catch {
    return base;
  }
}

export function saveSettings(storage: Storage | null, settings: PageSettings): void {
  if (!storage) return;
  try {
    storage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* private mode can refuse the write */
  }
}

export function applyTheme(theme: ThemeName): void {
  document.documentElement.setAttribute("data-theme", theme);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#f4f4f1" : "#0a0a0a");
}

export function applyVoiceSilence(ms: number): number {
  setEndOfUtteranceSilence(ms);
  return ms;
}

export function browserStorage(kind: "local" | "session"): Storage | null {
  try {
    const store = kind === "local" ? window.localStorage : window.sessionStorage;
    const probe = "openpi.probe";
    store.setItem(probe, "1");
    store.removeItem(probe);
    return store;
  } catch {
    return null;
  }
}
