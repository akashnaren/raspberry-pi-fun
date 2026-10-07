import { cancelAttach } from "../attach/upload";
import { clearFollowQueue } from "../chat/follow-queue-view";
import { autoGrow } from "../chat/send";
import { paint } from "../chat/transcript";
import { abortImageJobs } from "../chat/turn-images";
import { byId } from "../core/dom";
import { freshId } from "../core/session";
import { applyTheme, browserStorage, saveSettings, type ThemeName } from "../core/settings";
import { CHAT_KEY, ui } from "../core/state";
import { turns } from "../main";
import { refreshMemoryRing } from "../memory/ring";
import { voiceNote } from "../voice/mode";
import { endOfUtteranceSilence } from "../voice/speech";
import { paintBrand } from "./brand";
import { setSettingsOpen, setSourcesOpen } from "./panels";

export function persistSettings(): void {
  ui.pageSettings = {
    theme: (document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark") as ThemeName,
    mode: ui.modelMode,
    thinking: ui.thinking,
    voiceSilenceMs: endOfUtteranceSilence(),
    enterToSend: ui.enterToSend,
    pictures: ui.picturesOn,
  };
  saveSettings(browserStorage("local"), ui.pageSettings);
}

export function paintThinking(): void {
  document.querySelectorAll("[data-think]").forEach((node) => {
    node.classList.toggle("on", node.getAttribute("data-think") === ui.thinking);
  });
}

export function paintModelMode(): void {
  const label = ui.modelMode === "flash" ? "Flash" : ui.modelMode === "pro" ? "Pro" : "Auto";
  const name = document.getElementById("modeLabel");
  if (name) name.textContent = label;
  document.querySelectorAll("[data-mode]").forEach((node) => {
    const on = node.getAttribute("data-mode") === ui.modelMode;
    node.classList.toggle("on", on);
    if (node.getAttribute("role") === "menuitemradio") node.setAttribute("aria-checked", on ? "true" : "false");
  });
}

export function paintThemeChoice(): void {
  const theme = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  document.querySelectorAll("[data-theme-choice]").forEach((node) => {
    node.classList.toggle("on", node.getAttribute("data-theme-choice") === theme);
  });
}

export function setThinking(next: string): void {
  ui.thinking = next === "low" || next === "high" ? next : "medium";
  paintThinking();
  persistSettings();
}

export function setModelMode(next: string): void {
  ui.modelMode = next === "flash" || next === "pro" ? next : "auto";
  paintModelMode();
  closeInfoTips();
  const menu = document.getElementById("modePop");
  if (menu) menu.hidden = true;
  byId("modeBtn").setAttribute("aria-expanded", "false");
  persistSettings();
}

export function setTheme(next: string): void {
  const theme: ThemeName = next === "light" ? "light" : "dark";
  applyTheme(theme);
  paintThemeChoice();
  persistSettings();
}

export function infoTip(btn: HTMLElement): HTMLElement | null {
  const id = btn.getAttribute("aria-describedby") || "";
  return id ? document.getElementById(id) : null;
}

export function closeInfoTips(): void {
  ui.pinnedInfo = null;
  document.querySelectorAll(".info-dot").forEach((node) => {
    node.setAttribute("aria-expanded", "false");
    const tip = infoTip(node as HTMLElement);
    if (tip) tip.hidden = true;
  });
}

export function openInfo(btn: HTMLElement, pin: boolean): void {
  document.querySelectorAll(".info-dot").forEach((node) => {
    if (node === btn) return;
    node.setAttribute("aria-expanded", "false");
    const other = infoTip(node as HTMLElement);
    if (other) other.hidden = true;
  });
  if (ui.pinnedInfo && ui.pinnedInfo !== btn) ui.pinnedInfo = null;
  const tip = infoTip(btn);
  if (!tip) return;
  btn.setAttribute("aria-expanded", "true");
  tip.hidden = false;
  if (pin) ui.pinnedInfo = btn;
}

export function closeModeMenu(): void {
  const menu = document.getElementById("modePop");
  if (menu) menu.hidden = true;
  byId("modeBtn").setAttribute("aria-expanded", "false");
}

export function dismissPopovers(event?: Event): void {
  const target = event?.target as { closest?: (selector: string) => unknown } | null;
  if (target && typeof target.closest === "function" && target.closest(".info-slot, #modePop, #modeBtn")) {
    return;
  }
  closeInfoTips();
  closeModeMenu();
}

export function newChat(): void {
  abortImageJobs();
  clearFollowQueue();
  ui.chatId = freshId();
  browserStorage("session")?.setItem(CHAT_KEY, ui.chatId);
  ui.chatEpoch += 1;
  ui.stopAsked = true;
  ui.turnCtrl?.abort();
  ui.sending = false;
  turns.length = 0;
  const box = byId<HTMLTextAreaElement>("q");
  box.value = "";
  autoGrow(box);
  cancelAttach();
  voiceNote("");
  setSourcesOpen(false);
  setSettingsOpen(false);
  closeInfoTips();
  closeModeMenu();
  paint();
  paintBrand();
  box.focus();
  void refreshMemoryRing();
}
