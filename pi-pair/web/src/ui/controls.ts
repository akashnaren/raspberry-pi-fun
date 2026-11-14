import { writeWebSearch } from "../chat/web-search";
import { browserStorage, saveSettings } from "../core/settings";
import { ui } from "../core/state";
import { endOfUtteranceSilence } from "../voice/speech";

export function persistSettings(): void {
  ui.pageSettings = {
    theme: "dark",
    mode: ui.modelMode,
    thinking: ui.thinking,
    voiceSilenceMs: endOfUtteranceSilence(),
    enterToSend: ui.enterToSend,
    pictures: ui.picturesOn,
  };
  saveSettings(browserStorage("local"), ui.pageSettings);
}

export function paintWebSearch(): void {
  const button = document.getElementById("webSearch");
  if (!button) return;
  button.classList.toggle("on", ui.webSearch);
  button.setAttribute("aria-pressed", ui.webSearch ? "true" : "false");
}

export function setWebSearch(on: boolean): void {
  ui.webSearch = on;
  writeWebSearch(browserStorage("session"), on);
  paintWebSearch();
}

export function paintThinking(): void {
  document.querySelectorAll("[data-think]").forEach((node) => {
    node.classList.toggle("on", node.getAttribute("data-think") === ui.thinking);
  });
}

function asButton(node: EventTarget | null): HTMLButtonElement | null {
  if (!node || typeof node !== "object" || !("tagName" in node)) return null;
  if ((node as Element).tagName !== "BUTTON") return null;
  return node as HTMLButtonElement;
}

export function modelLabel(mode: string): string {
  if (mode === "flash") return "Flash";
  if (mode === "pro") return "Pro";
  return "Auto";
}

export function paintModelMode(): void {
  const label = modelLabel(ui.modelMode);
  const current = document.getElementById("modelCurrent");
  if (current) current.textContent = label;
  const trigger = document.getElementById("modelTrigger");
  if (trigger) trigger.setAttribute("aria-label", "Model: " + label);
  document.querySelectorAll(".model-option[data-mode]").forEach((node) => {
    const on = node.getAttribute("data-mode") === ui.modelMode;
    node.classList.toggle("on", on);
    node.setAttribute("aria-checked", on ? "true" : "false");
  });
}

export function modelMenuOpen(): boolean {
  return document.getElementById("modelTrigger")?.getAttribute("aria-expanded") === "true";
}

export function setModelMenuOpen(open: boolean): void {
  const list = document.getElementById("modelList");
  const trigger = document.getElementById("modelTrigger");
  if (!list || !trigger) return;
  list.hidden = !open;
  trigger.setAttribute("aria-expanded", open ? "true" : "false");
  if (!open) return;
  const selected = list.querySelector('[aria-checked="true"]');
  const target = asButton(selected ?? list.querySelector(".model-option"));
  target?.focus();
}

export function closeModelMenu(focusTrigger: boolean): void {
  const wasOpen = modelMenuOpen();
  setModelMenuOpen(false);
  if (!focusTrigger || !wasOpen) return;
  asButton(document.getElementById("modelTrigger"))?.focus();
}

export function moveModelMenu(key: string): void {
  const options = [...document.querySelectorAll(".model-option")].flatMap((node) => {
    const button = asButton(node);
    return button ? [button] : [];
  });
  if (!options.length) return;
  const current = options.findIndex((node) => node === document.activeElement);
  if (key === "Home") {
    options[0]?.focus();
    return;
  }
  if (key === "End") {
    options[options.length - 1]?.focus();
    return;
  }
  const delta = key === "ArrowDown" ? 1 : -1;
  const start = current === -1
    ? (key === "ArrowDown" ? 0 : options.length - 1)
    : (current + delta + options.length) % options.length;
  options[start]?.focus();
}

export function setThinking(next: string): void {
  ui.thinking = next === "low" || next === "high" ? next : "medium";
  paintThinking();
  persistSettings();
}

export function setModelMode(next: string): void {
  ui.modelMode = next === "flash" || next === "pro" ? next : "auto";
  paintModelMode();
  persistSettings();
}
