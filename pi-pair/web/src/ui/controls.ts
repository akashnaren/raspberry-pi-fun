import { cancelAttach } from "../attach/upload";
import { clearFollowQueue } from "../chat/follow-queue-view";
import { autoGrow } from "../chat/send";
import { paint } from "../chat/transcript";
import { abortImageJobs } from "../chat/turn-images";
import { writeWebSearch } from "../chat/web-search";
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

function clearTipStyle(tip: HTMLElement): void {
  tip.style.position = "";
  tip.style.left = "";
  tip.style.top = "";
  tip.style.right = "";
  tip.style.bottom = "";
  tip.style.transform = "";
  tip.style.maxWidth = "";
  tip.style.zIndex = "";
  tip.style.width = "";
}

function restoreTip(tip: HTMLElement): void {
  clearTipStyle(tip);
  const btn = document.querySelector(`.info-dot[aria-describedby="${tip.id}"]`);
  const slot = btn?.closest(".info-slot");
  if (slot && tip.parentElement !== slot) slot.appendChild(tip);
}

export function hideInfo(btn: HTMLElement): void {
  btn.setAttribute("aria-expanded", "false");
  const tip = infoTip(btn);
  if (!tip) return;
  tip.hidden = true;
  restoreTip(tip);
}

export function placeInfoTip(btn: HTMLElement, tip: HTMLElement): void {
  const pad = 12;
  watchInfoTips();
  if (tip.parentElement !== document.body) document.body.appendChild(tip);
  tip.style.position = "fixed";
  tip.style.zIndex = "200";
  tip.style.maxWidth = "min(260px, calc(100vw - 24px))";
  tip.style.width = "max-content";
  tip.style.right = "auto";
  tip.style.bottom = "auto";
  tip.style.transform = "none";
  tip.style.left = "0px";
  tip.style.top = "0px";
  const br = btn.getBoundingClientRect();
  const tr = tip.getBoundingClientRect();
  const width = Math.min(tr.width || 260, Math.max(pad, window.innerWidth - pad * 2));
  let left = br.left + br.width / 2 - width / 2;
  left = Math.max(pad, Math.min(left, window.innerWidth - pad - width));
  const above = br.top - (tr.height || 0) - 6;
  const below = br.bottom + 6;
  let top = above >= pad ? above : below;
  const maxTop = window.innerHeight - pad - (tr.height || 0);
  if (Number.isFinite(maxTop)) top = Math.max(pad, Math.min(top, maxTop));
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function repositionOpenTips(): void {
  document.querySelectorAll('.info-dot[aria-expanded="true"]').forEach((node) => {
    const tip = infoTip(node as HTMLElement);
    if (tip && !tip.hidden) placeInfoTip(node as HTMLElement, tip);
  });
}

let watchingTips = false;
export function watchInfoTips(): void {
  if (watchingTips) return;
  watchingTips = true;
  window.addEventListener("resize", repositionOpenTips);
  window.addEventListener("scroll", repositionOpenTips, true);
}

export function closeInfoTips(): void {
  ui.pinnedInfo = null;
  document.querySelectorAll(".info-dot").forEach((node) => {
    hideInfo(node as HTMLElement);
  });
}

export function openInfo(btn: HTMLElement, pin: boolean): void {
  document.querySelectorAll(".info-dot").forEach((node) => {
    if (node === btn) return;
    hideInfo(node as HTMLElement);
  });
  if (ui.pinnedInfo && ui.pinnedInfo !== btn) ui.pinnedInfo = null;
  const tip = infoTip(btn);
  if (!tip) return;
  btn.setAttribute("aria-expanded", "true");
  tip.hidden = false;
  placeInfoTip(btn, tip);
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
