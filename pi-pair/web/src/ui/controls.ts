import { cancelAttach } from "../attach/upload";
import { clearFollowQueue } from "../chat/follow-queue-view";
import { autoGrow } from "../chat/send";
import { paint } from "../chat/transcript";
import { abortImageJobs } from "../chat/turn-images";
import { writeWebSearch } from "../chat/web-search";
import { browserStorage, saveSettings } from "../core/settings";
import { CHAT_KEY, ui } from "../core/state";
import { freshId } from "../core/session";
import { turns } from "../main";
import { refreshMemoryRing } from "../memory/ring";
import { voiceNote } from "../voice/mode";
import { endOfUtteranceSilence } from "../voice/speech";
import { paintBrand } from "./brand";
import { setSourcesOpen } from "./panels";

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

export function paintModelMode(): void {
  const seg = document.getElementById("modelSeg");
  if (seg) seg.setAttribute("data-active", ui.modelMode);
  document.querySelectorAll(".model-seg [data-mode]").forEach((node) => {
    const on = node.getAttribute("data-mode") === ui.modelMode;
    node.classList.toggle("on", on);
    if (node.getAttribute("role") === "radio") {
      node.setAttribute("aria-checked", on ? "true" : "false");
      (node as HTMLButtonElement).tabIndex = on ? 0 : -1;
    }
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
  persistSettings();
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
  const box = document.getElementById("q") as HTMLTextAreaElement | null;
  if (!box) return;
  box.value = "";
  autoGrow(box);
  cancelAttach();
  voiceNote("");
  setSourcesOpen(false);
  paint();
  paintBrand();
  box.focus();
  void refreshMemoryRing();
}