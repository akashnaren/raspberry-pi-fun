import type { DocCard } from "./attach/message";
import { abortAttachForNavigation, cancelAttach, loadFile, releaseAttachButton } from "./attach/upload";
import { paintModelTips, refresh } from "./chat/health";
import { autoGrow, composerHasDraft, send, stopCurrent, syncSend } from "./chat/send";
import { paint, restoreTurns } from "./chat/transcript";
import { abortImageJobs } from "./chat/turn-images";
import { byId, els } from "./core/dom";
import { HEALTH_POLL_MS, VISIBILITY_SETTLE_MS, shouldPollHealth } from "./core/presence";
import { sessionHeaders } from "./core/session";
import { applyTheme, applyVoiceSilence } from "./core/settings";
import { ui } from "./core/state";
import type { Turn } from "./core/types";
import { compactMemory, hideMemPopSoon, holdRingTitle, refreshMemoryRing, showMemPop } from "./memory/ring";
import { bootSplash, paintBrand } from "./ui/brand";
import { closeInfoTips, dismissPopovers, infoTip, newChat, openInfo, paintModelMode, paintThemeChoice, paintThinking, persistSettings, setModelMode, setTheme, setThinking } from "./ui/controls";
import { setSettingsOpen, setSourcesOpen } from "./ui/panels";
import { armBarge, endVoiceMode, ensureOrb, releaseVoice, setVoiceState, toggleVoice, toggleVoiceMode } from "./voice/mode";
import { firstSpokenSentence, whenSpeechEnds, whenSpeechPulses, whenSpeechStarts } from "./voice/speech";
export { loadFile } from "./attach/upload";
export { ringDashOffset, refreshMemoryRing } from "./memory/ring";

export const FLASH_TIP = "Fast answers for everyday questions.";
export const PRO_TIP = "Slower, more careful answers for harder questions.";
export const turns: Turn[] = [];
export const serviceLines: string[] = [];
export const followExtra = new Map<number, { text: string; hidden: string; attachment: DocCard | null }>();
export const ATTACH_TIMEOUT_MS = 60_000;

export const VOICE_CLASSES = ["listening", "heard", "thinking", "speaking", "error"];

byId("go").onclick = () => {
  if (ui.uploading || ui.attachError) return;
  if (ui.sending && !composerHasDraft()) {
    stopCurrent();
    return;
  }
  if (!composerHasDraft()) {
    toggleVoiceMode();
    return;
  }
  void send();
};
byId("stop").onclick = () => {
  if (ui.uploading || ui.attachError) return;
  stopCurrent();
};
const composer = byId<HTMLTextAreaElement>("q");
els.composer = composer;

composer.addEventListener("input", () => {
  autoGrow(composer);
  syncSend();
  paintBrand();
});
composer.addEventListener("keyup", () => paintBrand());
composer.addEventListener("compositionstart", () => paintBrand(true));
syncSend();
composer.addEventListener("keydown", (event) => {
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) paintBrand(true);
  if (event.key !== "Enter") return;
  if (event.shiftKey) return;
  if (ui.uploading || ui.attachError) {
    event.preventDefault();
    return;
  }
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    const box = event.target as HTMLTextAreaElement;
    const start = box.selectionStart ?? 0;
    const end = box.selectionEnd ?? start;
    const next = box.value.slice(0, start) + "\n" + box.value.slice(end);
    box.value = next;
    box.selectionStart = box.selectionEnd = start + 1;
    autoGrow(box);
    return;
  }
  if (!ui.enterToSend) return;
  event.preventDefault();
  void send();
});

document.querySelectorAll("[data-think]").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = () => setThinking(btn.getAttribute("data-think") || "medium");
});
document.querySelectorAll("[data-mode]").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = (event) => {
    event.stopPropagation();
    setModelMode(btn.getAttribute("data-mode") || "auto");
  };
});

document.querySelectorAll(".info-slot").forEach((slot) => {
  const btn = slot.querySelector(".info-dot") as HTMLButtonElement | null;
  if (!btn) return;
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (ui.pinnedInfo === btn) {
      ui.pinnedInfo = null;
      btn.setAttribute("aria-expanded", "false");
      const tip = infoTip(btn);
      if (tip) tip.hidden = true;
      return;
    }
    openInfo(btn, true);
  });
  slot.addEventListener("pointerenter", (event) => {
    const kind = (event as PointerEvent).pointerType || "";
    if (kind === "touch" || kind === "pen") return;
    if (!window.matchMedia("(pointer: fine)").matches) return;
    openInfo(btn, false);
  });
  slot.addEventListener("pointerleave", () => {
    if (ui.pinnedInfo === btn) return;
    btn.setAttribute("aria-expanded", "false");
    const tip = infoTip(btn);
    if (tip) tip.hidden = true;
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const pop = document.getElementById("memPop");
  if (pop) pop.hidden = true;
  dismissPopovers();
  setSettingsOpen(false);
  setSourcesOpen(false);
  if (ui.voiceOn) endVoiceMode();
  byId<HTMLTextAreaElement>("q").focus();
});
document.querySelectorAll("[data-theme-choice]").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = () => setTheme(btn.getAttribute("data-theme-choice") || "dark");
});
byId("modeBtn").onclick = (event) => {
  event.stopPropagation();
  closeInfoTips();
  const menu = byId("modePop");
  const open = menu.hidden;
  menu.hidden = !open;
  byId("modeBtn").setAttribute("aria-expanded", open ? "true" : "false");
};
document.addEventListener("pointerdown", (event) => dismissPopovers(event), true);
document.addEventListener("click", (event) => dismissPopovers(event));
const enterBox = byId<HTMLInputElement>("enterSend");
els.enterBox = enterBox;

enterBox.checked = ui.enterToSend;
enterBox.addEventListener("change", () => {
  ui.enterToSend = enterBox.checked;
  persistSettings();
});
const picturesBox = byId<HTMLInputElement>("pictures");
els.picturesBox = picturesBox;

picturesBox.checked = ui.picturesOn;
picturesBox.addEventListener("change", () => {
  ui.picturesOn = picturesBox.checked;
  if (!ui.picturesOn) abortImageJobs();
  persistSettings();
});
applyTheme(ui.pageSettings.theme);
applyVoiceSilence(ui.pageSettings.voiceSilenceMs);
paintThinking();
paintModelMode();
paintThemeChoice();
whenSpeechStarts((text) => {
  if (!ui.voiceOn) return;
  ui.speakingLine = text || ui.speakingLine;
  const line = firstSpokenSentence(text) || text || "Speaking";
  setVoiceState("speaking", line);
  armBarge();
});
whenSpeechPulses(() => {
  if (!ui.voiceOn) return;
  ensureOrb()?.pulse();
});
whenSpeechEnds(releaseVoice);
byId("btnVoice").onclick = () => toggleVoice();
const voiceSend = document.getElementById("voiceSend");
els.voiceSend = voiceSend;

if (voiceSend) voiceSend.onclick = () => ui.voiceUtterance?.flush();
const voiceEnd = document.getElementById("voiceEnd");
els.voiceEnd = voiceEnd;

if (voiceEnd) voiceEnd.onclick = () => endVoiceMode();
byId("btnNew").onclick = () => newChat();
export const RING_C = 56.55;

const memoryButton = document.getElementById("btnMemory");
els.memoryButton = memoryButton;

const memoryAnchor = memoryButton?.closest(".mem-anchor");
els.memoryAnchor = memoryAnchor;

if (memoryButton && memoryAnchor) {
  memoryAnchor.addEventListener("pointerenter", () => showMemPop());
  memoryAnchor.addEventListener("pointerleave", () => hideMemPopSoon());
  memoryAnchor.addEventListener("focusin", () => showMemPop());
  memoryAnchor.addEventListener("focusout", (event: Event) => {
    const next = event instanceof FocusEvent ? event.relatedTarget : null;
    if (next instanceof Node && memoryAnchor.contains(next)) return;
    hideMemPopSoon();
  });
  memoryButton.addEventListener("click", () => {
    void compactMemory();
  });
  memoryButton.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown") return;
    event.preventDefault();
    const pop = document.getElementById("memPop");
    if (pop) pop.hidden = false;
    document.getElementById("memCompact")?.focus();
  });
}
document.getElementById("memCompact")?.addEventListener("click", () => {
  void compactMemory();
});
document.getElementById("memClear")?.addEventListener("click", () => {
  const button = document.getElementById("memClear");
  if (!button) return;
  if (button.dataset.armed === "1") {
    window.clearTimeout(ui.memClearTimer);
    button.dataset.armed = "";
    button.textContent = "Clear memory";
    void fetch("/v1/memory", { method: "DELETE", headers: sessionHeaders() }).then(() =>
      refreshMemoryRing().then(() => {
        holdRingTitle("Memory cleared", 2000);
        const pop = document.getElementById("memPopText");
        if (pop) pop.textContent = "Memory cleared";
      }),
    );
    return;
  }
  button.textContent = "Tap again to clear";
  button.dataset.armed = "1";
  window.clearTimeout(ui.memClearTimer);
  ui.memClearTimer = window.setTimeout(() => {
    button.dataset.armed = "";
    button.textContent = "Clear memory";
  }, 3000);
});
void refreshMemoryRing();
byId("btnIo").onclick = () => setSettingsOpen(true);
byId("btnCloseIo").onclick = () => setSettingsOpen(false);
byId("overlay").onclick = () => {
  setSettingsOpen(false);
  setSourcesOpen(false);
};
byId("sourcesClose").onclick = () => setSourcesOpen(false);
byId("btnAttach").onclick = () => byId<HTMLInputElement>("attach").click();
byId<HTMLInputElement>("attach").onchange = (event) => {
  const input = event.target as HTMLInputElement;
  loadFile(input.files && input.files[0]);
};
byId("fileClear").onclick = () => cancelAttach();
bootSplash();
paintModelTips();
composer.addEventListener("paste", (event) => {
  const items = event.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.kind === "file") {
      const file = item.getAsFile();
      if (file) {
        event.preventDefault();
        loadFile(file);
        return;
      }
    }
  }
});
window.addEventListener("pagehide", abortAttachForNavigation);
window.addEventListener("unload", abortAttachForNavigation);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) cancelAttach();
});
releaseAttachButton();
syncSend();
restoreTurns();
if (turns.length) paint();
void refresh();
window.setInterval(() => {
  if (!shouldPollHealth(document.hidden)) return;
  void refresh();
}, HEALTH_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  ui.resumedAt = Date.now();
  window.setTimeout(() => {
    if (!shouldPollHealth(document.hidden)) return;
    const resume = ui.resumeSend;
    ui.resumeSend = null;
    if (resume) resume();
    void refresh();
  }, VISIBILITY_SETTLE_MS);
});
