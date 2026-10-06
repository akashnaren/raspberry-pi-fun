import { docExcerpt, modelUserContent, userMessagePieces, type DocCard } from "./attach";
import { mountDiagrams } from "./diagram";
import { renderMarkdown, renderStreamingMarkdown } from "./markdown";
import { paintMicButton } from "./mic-button";
import { HEALTH_POLL_MS, serviceView, shouldPollHealth, shouldSoftRetry, softRetryDelay, suppressOfflineBanner, VISIBILITY_SETTLE_MS, type HealthSnapshot } from "./presence";
import { primaryKind, primaryLabel } from "./primary-action";
import { applyTheme, applyVoiceSilence, browserStorage, loadSettings, saveSettings, type ModelMode, type PageSettings, type ThinkLevel, type ThemeName } from "./settings";
import { renderFailedSearch, renderSourcesPanelBody, renderSourcesPill, type PanelDetail, type SourceLink as PillSource } from "./sources";
import { scrubAssistant } from "./copy";
import { BIG_LINE, friendlyError, WAITING_LINE } from "./errors";
import { dropFollow, enqueueFollow, renderFollowQueue, takeFollow, type FollowItem } from "./follow-queue";
import { appendBrandMark, navigationType, shouldPlaySplash, SPLASH_HOLD_MS, SPLASH_KEY } from "./splash";
import { createUtteranceHold, endOfUtteranceSilence, isSoloStop, noteSpokenDelta, shouldBargeIn, speakText, speechPending, speechReady, startListening, stopSpeaking, turnFromRecognition, whenSpeechEnds, whenSpeechPulses, whenSpeechStarts } from "./voice";

type Role = "user" | "assistant";
type StageName = "loading" | "waiting" | "thinking" | "searching" | "answering";

interface SourceLink {
  title: string;
  url: string;
}

type ModelChoice = ModelMode;

interface SearchInfo {
  status: string;
  sources: SourceLink[];
}

interface Turn {
  role: Role;
  content: string;
  hidden?: string;
  attachment?: DocCard | null;
  effort?: string;
  search?: SearchInfo | null;
  stages?: StageName[];
  mode?: string;
  route?: string;
  thought?: string;
  thoughtSeconds?: number;
}

interface HealthBody extends HealthSnapshot {
  peers?: { models?: string[] }[];
  modes?: { flash?: string; pro?: string };
}

interface LiveTurn {
  root: HTMLElement;
  body: HTMLElement;
  stagesEl: HTMLElement;
  seen: StageName[];
  search: SearchInfo | null;
  pushStatus: (
    name: StageName,
    search: SearchInfo | null,
    queue?: { position?: number; eta_s?: number } | null,
  ) => void;
  setText: (text: string) => void;
  setThought: (text: string, live: boolean, seconds: number) => void;
  clearThought: () => void;
  finish: (text: string, failed: boolean, prompt: string, search: SearchInfo | null, stages: StageName[]) => void;
  markErr: () => void;
  armPro: () => void;
}

let healthAfterSend = false;
const FLASH_TIP = "Fast answers for everyday questions.";
const PRO_TIP = "Slower, more careful answers for harder questions.";
const turns: Turn[] = [];
let sending = false;
let stopAsked = false;
let chatEpoch = 0;
let requestId = "";
let turnCtrl: AbortController | null = null;
let pageSettings: PageSettings = loadSettings(browserStorage("local"));
let thinking: ThinkLevel = pageSettings.thinking;
let modelMode: ModelChoice = pageSettings.mode;
let enterToSend = pageSettings.enterToSend;
let listening = false;
let dictating = false;
let dictated = "";
let voiceOn = false;
let voiceHold = false;
let listenHandle: { stop: () => void } | null = null;
let cancelUtterance: (() => void) | null = null;
let pendingDoc: DocCard | null = null;
let resumedAt = 0;
let serviceSig = "";
let resumeSend: (() => void) | null = null;
let graceTimer = 0;
let bargeHandle: { stop: () => void } | null = null;
let pendingBarge = "";
let speakingLine = "";
const serviceLines: string[] = [];
let attachSerial = 0;
let uploading = false;
let attachError = false;
let followQueue: FollowItem[] = [];
let followNextId = 1;
const followExtra = new Map<number, { text: string; hidden: string; attachment: DocCard | null }>();
let voiceUtterance: ReturnType<typeof createUtteranceHold> | null = null;

const ATTACH_BYTES = 4 * 1024 * 1024;

interface AttachmentResult {
  text?: string;
  route?: string;
  truncated?: boolean;
  error?: string;
}

function el(tag: string, cls?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function byId<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error("missing " + id);
  return node as T;
}

function modeModel(): string {
  return modelMode === "pro" ? "pro" : "flash";
}

function withoutThinkTags(text: string): string {
  return String(text || "")
    .replace(/<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>/gi, "")
    .replace(/<think(?:ing)?>[\s\S]*$/gi, "")
    .replace(/<\/think(?:ing)?>/gi, "");
}

function thoughtSummary(seconds: number, live: boolean): string {
  if (live) return "Thinking…";
  return "Thought for " + Math.max(1, seconds) + "s";
}

function thoughtPanel(text: string, seconds: number, live: boolean): HTMLDetailsElement {
  const box = document.createElement("details");
  box.className = "thought";
  if (live) box.open = true;
  const summary = document.createElement("summary");
  summary.textContent = thoughtSummary(seconds, live);
  const body = el("div", "thought-body");
  body.textContent = text;
  box.append(summary, body);
  return box;
}

function persistSettings(): void {
  pageSettings = {
    theme: (document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark") as ThemeName,
    mode: modelMode,
    thinking,
    voiceSilenceMs: endOfUtteranceSilence(),
    enterToSend,
  };
  saveSettings(browserStorage("local"), pageSettings);
}

function hideEmpty(): void {
  document.getElementById("empty")?.remove();
}

function setSettingsOpen(on: boolean): void {
  byId("ioPanel").classList.toggle("open", on);
  if (on) byId("sourcesPanel").classList.remove("open");
  const sourcesOpen = byId("sourcesPanel").classList.contains("open");
  byId("overlay").classList.toggle("open", on || sourcesOpen);
}

function setBodyContent(node: HTMLElement, text: string, asMd: boolean, streaming = false): void {
  const shown = scrubAssistant(withoutThinkTags(text));
  if (asMd) {
    node.classList.add("md");
    node.innerHTML = streaming ? renderStreamingMarkdown(shown) : renderMarkdown(shown);
    mountDiagrams(node);
  } else {
    node.classList.remove("md");
    node.textContent = shown;
  }
}

function setSourcesOpen(on: boolean): void {
  const panel = byId("sourcesPanel");
  panel.classList.toggle("open", on);
  panel.setAttribute("aria-hidden", on ? "false" : "true");
  const settingsOpen = byId("ioPanel").classList.contains("open");
  byId("overlay").classList.toggle("open", on || settingsOpen);
}

function openSourcesPanel(detail: PanelDetail): void {
  const body = byId("sourcesBody");
  body.replaceChildren(renderSourcesPanelBody(document, detail));
  setSettingsOpen(false);
  setSourcesOpen(true);
  document.querySelectorAll(".sources-pill").forEach((pill) => {
    pill.setAttribute("aria-expanded", "false");
  });
}

function showSearch(parent: HTMLElement, status: string, sources: SourceLink[], stages: StageName[], prompt: string): void {
  if (status === "failed") {
    parent.appendChild(renderFailedSearch(document));
    return;
  }
  if (status !== "ok") return;
  const links = (sources || []).filter((src) => src && String(src.url || "").startsWith("http"));
  if (!links.length) return;
  const pill = renderSourcesPill(document, links as PillSource[]);
  const detail: PanelDetail = { status, sources: links, stages, prompt };
  pill.onclick = () => {
    const opening = !byId("sourcesPanel").classList.contains("open");
    if (!opening) {
      setSourcesOpen(false);
      pill.setAttribute("aria-expanded", "false");
      return;
    }
    openSourcesPanel(detail);
    pill.setAttribute("aria-expanded", "true");
  };
  parent.appendChild(pill);
}

function waitingCopy(queue: { position?: number; eta_s?: number } | null): string {
  const ahead = Number(queue?.position);
  const eta = Number(queue?.eta_s);
  if (!Number.isFinite(ahead) || ahead <= 0 || !Number.isFinite(eta) || eta <= 0) {
    return WAITING_LINE;
  }
  const seconds = Math.max(1, Math.round(eta));
  return "You're #" + Math.round(ahead) + ", about " + seconds + " s";
}

function stageText(name: StageName, search: SearchInfo | null): string {
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

function shownError(err: unknown): string {
  return friendlyError((err as { message?: string })?.message || err || "");
}

function visibleReply(text: string): boolean {
  return text.trim().length > 0;
}

function thumbIcon(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.6");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M8 11v8a1 1 0 0 0 1 1h7.2a2 2 0 0 0 1.9-1.4l1.3-5.2A2 2 0 0 0 17.5 11H14V7.2A2.2 2.2 0 0 0 11.8 5c-.5 0-.9.3-1.1.7L8 11zM8 11H5.5A1.5 1.5 0 0 0 4 12.5v5A1.5 1.5 0 0 0 5.5 19H8");
  svg.appendChild(path);
  return svg;
}

function showOffline(): void {
  if (suppressOfflineBanner(document.hidden, resumedAt, Date.now(), undefined, sending)) return;
  const banner = byId("banner");
  if (banner.classList.contains("on")) return;
  banner.className = "on";
  banner.replaceChildren(document.createTextNode("Can't reach the server."));
  const retry = el("button", "banner-retry", "Retry");
  retry.onclick = () => {
    banner.className = "";
    banner.replaceChildren();
    void refresh();
  };
  banner.appendChild(document.createTextNode(" "));
  banner.appendChild(retry);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function refreshAfterGrace(): void {
  if (graceTimer) return;
  const elapsed = resumedAt > 0 ? Date.now() - resumedAt : 0;
  const wait = Math.max(0, 2500 - elapsed) + 40;
  graceTimer = window.setTimeout(() => {
    graceTimer = 0;
    if (shouldPollHealth(document.hidden)) void refresh();
  }, wait);
}

async function refresh(): Promise<void> {
  if (!shouldPollHealth(document.hidden)) return;
  for (let attempt = 0; ; attempt += 1) {
    if (!shouldPollHealth(document.hidden)) return;
    try {
      const response = await fetch("/health", { method: "GET", cache: "no-store" });
      if (!response.ok) throw new Error("offline");
      const body = await response.json() as HealthBody;
      const banner = byId("banner");
      banner.className = "";
      banner.replaceChildren();
      paintModelTips();
      paintServices(body);
      return;
    } catch {
      if (suppressOfflineBanner(document.hidden, resumedAt, Date.now(), undefined, sending)) {
        if (sending) healthAfterSend = true;
        else refreshAfterGrace();
        return;
      }
      if (shouldSoftRetry(attempt)) {
        await delay(softRetryDelay(attempt));
        continue;
      }
      showOffline();
      return;
    }
  }
}

function paintModelTips(): void {
  const write = (id: string, text: string) => {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  };
  write("tip-menu-flash", FLASH_TIP);
  write("tip-set-flash", FLASH_TIP);
  write("tip-menu-pro", PRO_TIP);
  write("tip-set-pro", PRO_TIP);
}

function paintServices(body: HealthBody): void {
  const now = document.getElementById("serviceNow");
  const log = document.getElementById("serviceLog");
  if (!now || !log) return;
  const view = serviceView(body);
  now.textContent = view.now;
  if (view.signature !== serviceSig) {
    serviceSig = view.signature;
    const stamp = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    serviceLines.unshift(stamp + " · " + view.event);
    if (serviceLines.length > 8) serviceLines.length = 8;
  }
  log.replaceChildren();
  serviceLines.forEach((entry) => {
    log.appendChild(el("p", "service-line", entry));
  });
}

async function quietNetwork(): Promise<boolean> {
  if (suppressOfflineBanner(document.hidden, resumedAt, Date.now())) return true;
  await delay(VISIBILITY_SETTLE_MS);
  return suppressOfflineBanner(document.hidden, resumedAt, Date.now());
}

function armResumeSend(text: string, spoken: boolean): void {
  resumeSend = () => {
    void sendText(text, true, spoken);
  };
}

function attachLabel(parent: HTMLElement, prompt: string, answer: string): void {
  const row = el("div", "label-row");
  const up = el("button", "icon-btn") as HTMLButtonElement;
  const down = el("button", "icon-btn down") as HTMLButtonElement;
  const fix = el("button", "text-btn", "Correct") as HTMLButtonElement;
  const note = el("span", "label-note", "");
  up.type = "button";
  down.type = "button";
  fix.type = "button";
  up.appendChild(thumbIcon());
  down.appendChild(thumbIcon());
  up.setAttribute("aria-label", "Thumbs up");
  down.setAttribute("aria-label", "Thumbs down");
  fix.setAttribute("aria-label", "Corrected answer");
  const box = el("div", "fix-box");
  const input = document.createElement("textarea");
  input.rows = 2;
  input.placeholder = "Corrected answer";
  const save = el("button", "mini", "Save") as HTMLButtonElement;
  save.type = "button";
  box.appendChild(input);
  box.appendChild(save);
  let vote = "";
  const paintVote = () => {
    up.classList.toggle("on", vote === "up");
    down.classList.toggle("on", vote === "down");
  };
  async function sendLabel(next: string): Promise<void> {
    vote = next;
    paintVote();
    note.textContent = "saving";
    const correction = (input.value || "").trim();
    try {
      const response = await fetch("/v1/flywheel/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ vote, prompt, answer, correction }),
      });
      let payload: { error?: string } = {};
      try {
        payload = await response.json() as { error?: string };
      } catch {
        payload = {};
      }
      if (!response.ok) throw new Error(payload.error || "HTTP " + response.status);
      note.textContent = "Saved";
      window.setTimeout(() => {
        if (note.textContent === "Saved") note.textContent = "";
      }, 1400);
    } catch {
      note.textContent = "Not saved";
    }
  }
  up.onclick = () => void sendLabel("up");
  down.onclick = () => {
    box.classList.add("on");
    void sendLabel("down");
  };
  fix.onclick = () => box.classList.toggle("on");
  save.onclick = () => void sendLabel(vote || "down");
  row.appendChild(up);
  row.appendChild(down);
  row.appendChild(fix);
  row.appendChild(note);
  parent.appendChild(row);
  parent.appendChild(box);
}

function copyButton(text: string): HTMLButtonElement {
  const copy = el("button", "text-btn", "Copy") as HTMLButtonElement;
  copy.type = "button";
  copy.setAttribute("aria-label", "Copy");
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      copy.textContent = "Copied";
      window.setTimeout(() => {
        if (copy.textContent === "Copied") copy.textContent = "Copy";
      }, 1200);
    } catch {
      copy.textContent = "Copy failed";
    }
  };
  return copy;
}

function promptBefore(index: number): string {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (turns[i].role === "user") return turns[i].content;
  }
  return "";
}

function trail(stages: StageName[] | undefined, search: SearchInfo | null): HTMLElement | null {
  const names = (stages || []).filter((name) => name === "thinking" || name === "searching");
  if (!names.length) return null;
  const row = el("div", "trail");
  names.forEach((name, index) => {
    if (index) row.appendChild(el("span", "trail-dot", "·"));
    row.appendChild(el("span", "", stageText(name, search)));
  });
  return row;
}

function paint(): void {
  const log = byId("log");
  log.replaceChildren();
  if (!turns.length) {
    const empty = el("div", "empty");
    empty.id = "empty";
    empty.appendChild(el("p", "", "Ask anything."));
    log.appendChild(empty);
    return;
  }
  turns.forEach((item, index) => {
    if (item.role === "user") addUser(item.content, index);
    else addFinishedBot(item, index);
  });
  log.lastElementChild?.scrollIntoView({ block: "end" });
}

function docCardNode(card: DocCard): HTMLElement {
  const node = el("div", "doc-card");
  node.setAttribute("role", "group");
  node.setAttribute("aria-label", card.name);
  const kind = card.route === "ocr" ? "PDF" : "DOC";
  node.appendChild(el("span", "doc-card-kind", kind));
  const copy = el("div", "doc-card-copy");
  copy.appendChild(el("span", "doc-card-name", card.name));
  const kb = Math.max(1, Math.round((card.bytes || 0) / 1024));
  copy.appendChild(el("span", "doc-card-meta", kb + " KB"));
  if (card.excerpt) copy.appendChild(el("span", "doc-card-preview", card.excerpt));
  node.appendChild(copy);
  return node;
}

function addUser(text: string, index: number): HTMLElement {
  hideEmpty();
  const item = turns[index];
  const row = el("div", "msg user");
  row.dataset.index = String(index);
  userMessagePieces(text, item?.attachment || null).forEach((piece) => {
    if (piece.kind === "card" && piece.card) {
      row.appendChild(docCardNode(piece.card));
      return;
    }
    const body = el("div", "body");
    body.textContent = piece.text;
    row.appendChild(body);
  });
  const acts = el("div", "msg-actions");
  acts.appendChild(copyButton(text || item?.attachment?.name || ""));
  const edit = el("button", "text-btn", "Edit");
  edit.setAttribute("aria-label", "Edit");
  edit.onclick = () => beginEdit(index);
  acts.appendChild(edit);
  row.appendChild(acts);
  byId("log").appendChild(row);
  return row;
}

function beginEdit(index: number): void {
  if (sending) return;
  const item = turns[index];
  if (!item || item.role !== "user") return;
  const row = document.querySelector('.msg.user[data-index="' + index + '"]');
  if (!row) return;
  const body = row.querySelector(".body");
  const box = document.createElement("textarea");
  box.className = "edit-box";
  box.value = item.content;
  box.rows = 3;
  if (body) body.replaceWith(box);
  row.querySelector(".msg-actions")?.remove();
  const actions = el("div", "edit-actions");
  const cancel = el("button", "text-btn", "Cancel");
  const save = el("button", "mini", "Send");
  cancel.onclick = () => paint();
  save.onclick = () => {
    const next = box.value.trim();
    if (!next && !item.hidden) return;
    const hidden = item.hidden || "";
    const attachment = item.attachment || null;
    turns.splice(index);
    void sendText(next, false, false, { hidden, attachment });
  };
  actions.appendChild(cancel);
  actions.appendChild(save);
  row.appendChild(actions);
  box.focus();
}

function addFinishedBot(item: Turn, index: number): HTMLElement {
  hideEmpty();
  const row = el("div", "msg bot");
  row.dataset.index = String(index);
  const done = trail(item.stages, item.search || null);
  if (done) row.appendChild(done);
  let body: HTMLElement | null = null;
  if (item.thought) {
    row.appendChild(thoughtPanel(item.thought, item.thoughtSeconds || 1, false));
  }
  if (visibleReply(item.content)) {
    body = el("div", "body");
    setBodyContent(body, item.content, true);
    row.appendChild(body);
  }
  const asked = promptBefore(index);
  if (item.search) showSearch(row, item.search.status, item.search.sources, item.stages || [], asked);
  if (asked) attachLabel(row, asked, item.content);
  let labels = row.querySelector(".label-row");
  if (!labels) {
    labels = el("div", "label-row");
    row.appendChild(labels);
  }
  labels.appendChild(copyButton(item.content));
  if (index === turns.length - 1) {
    labels.appendChild(retryButton(() => regenerate()));
  }
  byId("log").appendChild(row);
  return row;
}

function retryIcon(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.7");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5");
  svg.appendChild(path);
  return svg;
}

function retryButton(onClick: () => void): HTMLButtonElement {
  const again = el("button", "icon-btn retry") as HTMLButtonElement;
  again.type = "button";
  again.setAttribute("aria-label", "Retry");
  again.title = "Retry";
  again.appendChild(retryIcon());
  again.onclick = onClick;
  return again;
}

function regenerate(): void {
  if (sending) return;
  if (!turns.length || turns[turns.length - 1].role !== "assistant") return;
  turns.pop();
  const last = turns[turns.length - 1];
  if (!last || last.role !== "user") return;
  paint();
  void sendText(last.content, true);
}

function motionReduced(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function addLiveBot(expectPro = false): LiveTurn {
  hideEmpty();
  let holdPro = expectPro;
  const row = el("div", "msg bot streaming");
  const stagesEl = el("div", "stages");
  stagesEl.setAttribute("aria-live", "polite");
  if (holdPro) {
    stagesEl.classList.add("pro-load");
    stagesEl.setAttribute("aria-busy", "true");
    stagesEl.setAttribute("aria-label", "Thinking");
  }
  const viewport = el("div", "stage-viewport");
  const dots = el("span", "pending");
  dots.appendChild(el("i"));
  dots.appendChild(el("i"));
  dots.appendChild(el("i"));
  viewport.appendChild(dots);
  stagesEl.appendChild(viewport);
  const body = el("div", "body");
  row.appendChild(stagesEl);
  byId("log").appendChild(row);
  row.scrollIntoView({ block: "end" });

  // Keep the text bubble off the page until the first reply token.
  function revealReply(text: string): void {
    if (!visibleReply(text)) return;
    const first = !body.dataset.filled;
    body.dataset.filled = "1";
    if (first) body.classList.add("arrived");
    if (!body.isConnected) row.appendChild(body);
  }

  let shown: StageName | null = null;
  let mdDue = 0;
  let mdTimer = 0;
  let mdLatest = "";
  const queued: StageName[] = [];
  let holding = false;
  let queueNote: { position?: number; eta_s?: number } | null = null;

  function retire(node: HTMLElement): void {
    if (node.classList.contains("leave")) return;
    if (motionReduced()) {
      node.remove();
      return;
    }
    node.classList.remove("on", "enter");
    node.classList.add("leave");
    node.addEventListener("animationend", (event) => {
      if (event.target !== node) return;
      node.remove();
    });
  }

  function stageLabel(node: HTMLElement, text: string): void {
    if (node.dataset.label === text) return;
    const label = node.querySelector(".stage-label");
    if (!label) return;
    node.dataset.label = text;
    if (motionReduced()) {
      label.textContent = text;
      return;
    }
    const current = label.querySelector(".label-in");
    if (current) {
      current.classList.remove("label-in");
      current.classList.add("label-out");
      current.addEventListener("animationend", () => current.remove(), { once: true });
    } else {
      label.replaceChildren();
    }
    label.appendChild(el("span", "label-in", text));
  }

  function labelFor(name: StageName): string {
    if (name === "waiting") return waitingCopy(queueNote);
    return stageText(name, live.search);
  }

  function renderStage(name: StageName): HTMLElement {
    viewport.querySelectorAll(".pending, .stage:not(.leave)").forEach((node) => {
      retire(node as HTMLElement);
    });
    const node = el("div", motionReduced() ? "stage on" : "stage enter on");
    node.dataset.stage = name;
    node.appendChild(el("span", "stage-dot"));
    const text = labelFor(name);
    const label = el("span", "stage-label");
    label.appendChild(el("span", motionReduced() ? "" : "label-in", text));
    node.appendChild(label);
    node.dataset.label = text;
    viewport.appendChild(node);
    shown = name;
    return node;
  }

  function yieldIfAnswer(): void {
    const ready = shown === "answering" || (holdPro && shown === "loading");
    if (ready && body.textContent && !queued.length && !holding) {
      stagesEl.classList.add("yield");
      stagesEl.removeAttribute("aria-busy");
    }
  }

  function pump(): void {
    if (holding || !row.isConnected) return;
    const name = queued.shift();
    if (!name) {
      yieldIfAnswer();
      return;
    }
    const node = renderStage(name);
    holding = true;
    const finish = () => {
      if (!holding) return;
      holding = false;
      if (!row.isConnected) return;
      pump();
    };
    if (motionReduced()) {
      finish();
      return;
    }
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      finish();
    };
    requestAnimationFrame(() => {
      if (!row.isConnected) {
        holding = false;
        return;
      }
      const onEnd = (event: AnimationEvent) => {
        if (event.target !== node || event.animationName !== "stage-enter") return;
        node.removeEventListener("animationend", onEnd);
        done();
      };
      node.addEventListener("animationend", onEnd);
      window.setTimeout(done, 1100);
    });
  }

  function enqueue(name: StageName): void {
    const current = viewport.querySelector(".stage:not(.leave)") as HTMLElement | null;
    if (shown === name && current) {
      stageLabel(current, labelFor(name));
      if (!holding && !queued.length) yieldIfAnswer();
      return;
    }
    if (queued[queued.length - 1] === name) return;
    queued.push(name);
    pump();
  }

  const live: LiveTurn = {
    root: row,
    body,
    stagesEl,
    seen: [],
    search: null,
    pushStatus(name, search, queue) {
      if (search && (search.status === "ok" || search.status === "failed")) live.search = search;
      if (queue && Number(queue.position) > 0) queueNote = queue;
      if (!live.seen.includes(name)) live.seen.push(name);
      enqueue(name);
      row.scrollIntoView({ block: "end" });
    },
    setText(text) {
      if (!visibleReply(text)) return;
      mdLatest = text;
      const paint = () => {
        const shownText = mdLatest;
        setBodyContent(body, shownText, true, true);
        revealReply(shownText);
        const current = viewport.querySelector(".stage:not(.leave)") as HTMLElement | null;
        if (current && shown) stageLabel(current, labelFor(shown));
        yieldIfAnswer();
        row.scrollIntoView({ block: "end" });
      };
      const now = performance.now();
      if (now < mdDue) {
        window.clearTimeout(mdTimer);
        mdTimer = window.setTimeout(() => {
          mdTimer = 0;
          window.requestAnimationFrame(() => {
            mdDue = performance.now() + 50;
            paint();
          });
        }, Math.max(0, mdDue - now));
        return;
      }
      mdDue = now + 50;
      paint();
    },
    setThought(text, liveThought, seconds) {
      const shownThought = String(text || "");
      if (!shownThought.trim()) {
        live.clearThought();
        return;
      }
      let box = row.querySelector("details.thought") as HTMLDetailsElement | null;
      if (!box) {
        box = thoughtPanel(shownThought, seconds, liveThought);
        if (body.isConnected) row.insertBefore(box, body);
        else row.appendChild(box);
      } else {
        const summary = box.querySelector("summary");
        const inner = box.querySelector(".thought-body");
        if (summary) summary.textContent = thoughtSummary(seconds, liveThought);
        if (inner) inner.textContent = shownThought;
        box.open = liveThought;
      }
      row.scrollIntoView({ block: "end" });
    },
    clearThought() {
      row.querySelector("details.thought")?.remove();
    },
    finish(text, failed, prompt, search, stages) {
      window.clearTimeout(mdTimer);
      mdTimer = 0;
      row.classList.remove("streaming");
      if (visibleReply(text)) {
        setBodyContent(body, text, !failed);
        revealReply(text);
      }
      if (!failed && search) showSearch(row, search.status, search.sources, stages, prompt);
      if (prompt && !failed) attachLabel(row, prompt, text);
      const done = trail(stages, search);
      if (done && !failed) row.insertBefore(done, stagesEl);
      stagesEl.remove();
      row.scrollIntoView({ block: "end", behavior: "smooth" });
    },
    markErr() {
      row.classList.add("err");
      row.classList.remove("streaming");
      stagesEl.removeAttribute("aria-busy");
    },
    armPro() {
      holdPro = true;
    },
  };
  return live;
}

function keepPartial(
  live: LiveTurn | null,
  text: string,
  prompt: string,
  effort: string,
  search: SearchInfo | null,
  stages: StageName[],
  mode = "",
  route = "",
  thought = "",
  thoughtSeconds = 0,
): void {
  const answer = withoutThinkTags(text);
  if (visibleReply(answer)) {
    turns.push({
      role: "assistant",
      content: answer,
      effort,
      search,
      stages,
      mode,
      route,
      thought,
      thoughtSeconds,
    });
    paint();
    return;
  }
  live?.root.remove();
}

function searchFrom(payload: { pi_search?: string; pi_sources?: SourceLink[] }, fallback: SearchInfo | null): SearchInfo | null {
  if (!payload.pi_search) return fallback;
  return {
    status: payload.pi_search,
    sources: Array.isArray(payload.pi_sources) ? payload.pi_sources : (fallback?.sources || []),
  };
}

function freshRequestId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") return cryptoObj.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const nibble = Math.floor(Math.random() * 16);
    const value = ch === "x" ? nibble : (nibble & 0x3) | 0x8;
    return value.toString(16);
  });
}

async function sendText(
  text: string,
  isRetry: boolean,
  spoken = false,
  extra?: { hidden?: string; attachment?: DocCard | null },
  attempt = 0,
): Promise<void> {
  if (sending) return;
  const epoch = chatEpoch;
  const mine = () => epoch === chatEpoch;
  if (!isRetry || !requestId) requestId = freshRequestId();
  const hidden = (extra?.hidden || "").trim();
  if (!isRetry && !text.trim() && !hidden) return;
  sending = true;
  stopAsked = false;
  stopSpeaking();
  syncSend();
  if (!isRetry) {
    turns.push({
      role: "user",
      content: text,
      hidden,
      attachment: extra?.attachment || null,
    });
    paint();
  }
  let voiced = false;
  let followUp: "retry" | "resume" | "" = "";
  const model = modeModel();
  const effort = thinking || "medium";
  const live = addLiveBot(modelMode === "pro");
  let textAccum = "";
  let thoughtAccum = "";
  let thoughtStarted = 0;
  let thoughtMs = 0;
  const thoughtSeconds = () => Math.max(1, Math.round((thoughtMs || 0) / 1000));
  const closeThought = () => {
    if (!thoughtAccum.trim()) {
      thoughtAccum = "";
      live.clearThought();
      return;
    }
    if (!thoughtMs) thoughtMs = Date.now() - (thoughtStarted || Date.now());
    live.setThought(thoughtAccum.trim(), false, thoughtSeconds());
  };
  let searchStatus = "";
  let searchSources: SourceLink[] = [];
  const stages: StageName[] = [];
  const showTurnError = (msg: string): void => {
    live.setText(msg);
    live.markErr();
    live.finish(msg, true, "", null, stages);
    live.root.appendChild(retryButton(() => {
      live.root.remove();
      void sendText(text, true);
    }));
  };
  const missOrRetry = (): void => {
    if (shouldSoftRetry(attempt)) {
      live.root.remove();
      followUp = "retry";
      return;
    }
    showTurnError(shownError(""));
  };
  void refresh();
  try {
    const body: {
      model: string;
      messages: { role: string; content: string }[];
      stream: boolean;
      think: string;
      pi_mode: string;
      pi_target: string;
      pi_mesh: string;
    } = {
      model,
      messages: [],
      stream: true,
      think: effort,
      pi_mode: modelMode,
      pi_target: "auto",
      pi_mesh: "on",
    };
    const sys = (byId<HTMLTextAreaElement>("sys").value || "").trim();
    if (sys) body.messages.push({ role: "system", content: sys });
    turns.forEach((turn) => {
      if (turn.role !== "user" && turn.role !== "assistant") return;
      const content = turn.role === "user"
        ? modelUserContent(turn.content, turn.hidden || "")
        : withoutThinkTags(turn.content);
      if (!content.trim()) return;
      body.messages.push({ role: turn.role, content });
    });

    let response: Response | null = null;
    let lastErr: unknown = null;
    for (let i = 0; i < 2; i += 1) {
      if (!mine() || stopAsked || !shouldPollHealth(document.hidden)) break;
      const attemptCtrl = new AbortController();
      turnCtrl = attemptCtrl;
      const timer = window.setTimeout(() => attemptCtrl.abort(), 180000);
      try {
        response = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "X-Pi-Target": "auto",
            "X-Pi-Mesh": "on",
            "X-Pi-Mode": modelMode,
            "X-Pi-Request-Id": requestId,
          },
          body: JSON.stringify(body),
          signal: attemptCtrl.signal,
          cache: "no-store",
        });
        lastErr = null;
        break;
      } catch (err) {
        if (stopAsked || !mine()) throw err;
        lastErr = err;
        const beforeResponse = err instanceof TypeError;
        if (!beforeResponse || i === 1 || !shouldPollHealth(document.hidden)) break;
        await delay(softRetryDelay(i));
      } finally {
        window.clearTimeout(timer);
      }
    }
    const searchNow = (): SearchInfo | null => (
      searchStatus ? { status: searchStatus, sources: searchSources } : null
    );
    if (!mine()) return;
    if (stopAsked) {
      keepPartial(live, textAccum, text, effort, searchNow(), stages, modelMode, "");
      return;
    }
    if (lastErr || !response) throw lastErr || new Error("no response");

    let streamedEffort = response.headers.get("X-Pi-Think") || effort;
    let streamedMode = response.headers.get("X-Pi-Mode") || modelMode;
    let streamedRoute = response.headers.get("X-Pi-Route") || "";
    if (streamedRoute === "pro") live.armPro();
    searchStatus = response.headers.get("X-Pi-Search") || "";
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("event-stream")) {
      const textBody = await response.text();
      let payload: {
        error?: string;
        pi_think?: string;
        pi_mode?: string;
        pi_route?: string;
        pi_search?: string;
        pi_sources?: SourceLink[];
        pi_stages?: StageName[];
        choices?: { message?: { content?: string } }[];
      } = {};
      let unreadable = false;
      try {
        payload = textBody ? JSON.parse(textBody) : {};
      } catch {
        unreadable = true;
        payload = {};
      }
      if (!response.ok) {
        showTurnError(shownError(payload.error || "HTTP " + response.status));
        return;
      }
      const answer = payload.choices?.[0]?.message?.content || "";
      const search = searchFrom(payload, searchNow());
      const doneStages = Array.isArray(payload.pi_stages) ? payload.pi_stages : stages;
      if (!mine()) return;
      if (unreadable || !visibleReply(answer)) {
        missOrRetry();
      } else {
        const nonStreamThought = String(
          (payload.choices?.[0]?.message as { reasoning_content?: string } | undefined)?.reasoning_content || "",
        ).trim();
        turns.push({
          role: "assistant",
          content: withoutThinkTags(answer),
          effort: payload.pi_think || streamedEffort,
          search,
          stages: doneStages,
          mode: payload.pi_mode || streamedMode,
          route: payload.pi_route || streamedRoute,
          thought: nonStreamThought,
          thoughtSeconds: nonStreamThought ? 1 : 0,
        });
        paint();
        if (spoken && speakText(answer)) voiced = true;
      }
    } else {

    const reader = response.body?.getReader();
    if (!reader) throw new Error("no stream");
    const decoder = new TextDecoder();
    let buf = "";
    let streamDone = false;
    let streamErr = "";
    while (!streamDone) {
      if (!mine()) return;
      if (stopAsked) break;
      const chunk = await reader.read();
      if (!mine()) return;
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      let nl = buf.indexOf("\n");
      while (nl >= 0) {
        let line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        const trimmed = line.trim();
        nl = buf.indexOf("\n");
        if (!trimmed || trimmed.startsWith(":")) continue;
        if (!trimmed.startsWith("data:")) continue;
        const payloadText = trimmed.slice(5).trim();
        if (payloadText === "[DONE]") {
          streamDone = true;
          break;
        }
        let payload: {
          error?: string;
          pi_status?: StageName;
          pi_think?: string;
          pi_mode?: string;
          pi_route?: string;
          pi_search?: string;
          pi_sources?: SourceLink[];
          pi_stages?: StageName[];
          pi_replace?: boolean;
          pi_reasoning_clear?: boolean;
          pi_queue?: { position?: number; eta_s?: number };
          choices?: { delta?: { content?: string; reasoning_content?: string } }[];
        };
        try {
          payload = JSON.parse(payloadText);
        } catch {
          continue;
        }
        if (payload.pi_search) {
          searchStatus = payload.pi_search;
          if (Array.isArray(payload.pi_sources)) searchSources = payload.pi_sources;
        }
        if (payload.pi_status) {
          live.pushStatus(payload.pi_status, searchNow(), payload.pi_queue || null);
          if (!stages.includes(payload.pi_status)) stages.push(payload.pi_status);
        }
        if (Array.isArray(payload.pi_stages)) {
          payload.pi_stages.forEach((name) => {
            if (!stages.includes(name)) stages.push(name);
          });
        }
        if (payload.error) {
          streamErr = String(payload.error);
          streamDone = true;
          break;
        }
        if (payload.pi_reasoning_clear) {
          thoughtAccum = "";
          thoughtStarted = 0;
          thoughtMs = 0;
          live.clearThought();
        }
        const reasoned = payload.choices?.[0]?.delta?.reasoning_content;
        if (reasoned) {
          if (!thoughtStarted) thoughtStarted = Date.now();
          thoughtAccum += reasoned;
          live.setThought(thoughtAccum, true, thoughtSeconds());
        }
        const delta = payload.choices?.[0]?.delta?.content;
        if (delta) {
          if (thoughtAccum && !thoughtMs) closeThought();
          textAccum = payload.pi_replace ? delta : textAccum + delta;
          const visible = withoutThinkTags(textAccum);
          live.setText(visible);
          if (spoken && !voiced && noteSpokenDelta(textAccum)) voiced = true;
        }
        if (payload.pi_think) streamedEffort = payload.pi_think;
        if (payload.pi_mode) streamedMode = payload.pi_mode;
        if (payload.pi_route) {
          streamedRoute = payload.pi_route;
          if (streamedRoute === "pro") live.armPro();
        }
      }
    }
    if (!mine()) return;
    if (stopAsked) {
      closeThought();
      keepPartial(
        live,
        textAccum,
        text,
        streamedEffort || effort,
        searchNow(),
        stages,
        streamedMode,
        streamedRoute,
        thoughtAccum,
        thoughtSeconds(),
      );
      return;
    }
    if (streamErr || (!response.ok && !visibleReply(textAccum))) {
      showTurnError(shownError(streamErr || "HTTP " + response.status));
      return;
    }
    if (!mine()) return;
    if (!visibleReply(textAccum)) {
      missOrRetry();
    } else {
      closeThought();
      const search = searchNow();
      turns.push({
        role: "assistant",
        content: withoutThinkTags(textAccum),
        effort: streamedEffort,
        search,
        stages,
        mode: streamedMode,
        route: streamedRoute,
        thought: thoughtAccum.trim(),
        thoughtSeconds: thoughtAccum.trim() ? thoughtSeconds() : 0,
      });
      paint();
      if (spoken && speakText(textAccum)) voiced = true;
    }
    }
  } catch (err) {
    if (!mine()) return;
    if (stopAsked) {
      keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, modelMode, "");
      return;
    }
    if (await quietNetwork()) {
      if (!mine()) return;
      if (visibleReply(textAccum)) {
        keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, modelMode, "");
      } else {
        live.root.remove();
        followUp = "resume";
      }
    } else if (!visibleReply(textAccum) && shouldSoftRetry(attempt)) {
      live.root.remove();
      followUp = "retry";
    } else if (visibleReply(textAccum)) {
      keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, modelMode, "");
    } else {
      showTurnError(shownError(err));
    }
  } finally {
    if (mine()) {
      sending = false;
      stopAsked = false;
      turnCtrl = null;
      syncSend();
      if (followUp !== "retry") byId<HTMLTextAreaElement>("q").focus();
      if (followUp !== "retry" && voiceOn && !speechPending()) releaseVoice();
    }
    if (followUp !== "retry") flushDeferredHealth();
    if (mine() && followUp !== "retry" && followUp !== "resume") flushFollowQueue();
  }
  if (!mine()) return;
  if (followUp === "retry") {
    await delay(softRetryDelay(attempt));
    if (!mine()) return;
    if (!shouldPollHealth(document.hidden)) {
      armResumeSend(text, spoken);
      return;
    }
    await sendText(text, true, spoken, extra, attempt + 1);
    return;
  }
  if (followUp === "resume") armResumeSend(text, spoken);
}

function flushDeferredHealth(): void {
  if (!healthAfterSend) return;
  healthAfterSend = false;
  if (shouldPollHealth(document.hidden)) void refresh();
}

function paintFollowQueue(): void {
  renderFollowQueue(byId("followQueue"), followQueue, (id) => {
    followQueue = dropFollow(followQueue, id);
    followExtra.delete(id);
    paintFollowQueue();
    syncSend();
  });
}

function clearFollowQueue(): void {
  followQueue = [];
  followExtra.clear();
  paintFollowQueue();
}

function queueDraft(text: string, hidden: string, attachment: DocCard | null): void {
  const label = text || (attachment && attachment.name) || "Attachment";
  const result = enqueueFollow(followQueue, label, followNextId);
  if (!result.added) {
    voiceNote("Three messages are already waiting.");
    return;
  }
  followQueue = result.items;
  followNextId = result.nextId;
  const item = result.items[result.items.length - 1];
  followExtra.set(item.id, { text, hidden: hidden.trim(), attachment });
  const box = byId<HTMLTextAreaElement>("q");
  if (hidden.trim() || attachment) clearAttach();
  box.value = "";
  autoGrow(box);
  paintBrand();
  paintFollowQueue();
  syncSend();
}

function flushFollowQueue(): void {
  if (sending) return;
  const taken = takeFollow(followQueue);
  if (!taken.next) return;
  followQueue = taken.rest;
  const extra = followExtra.get(taken.next.id);
  followExtra.delete(taken.next.id);
  paintFollowQueue();
  void sendText(extra ? extra.text : taken.next.text, false, false, {
    hidden: extra?.hidden || "",
    attachment: extra?.attachment || null,
  });
}

function stopCurrent(): void {
  if (!sending) return;
  stopAsked = true;
  stopSpeaking();
  turnCtrl?.abort();
}

async function send(): Promise<void> {
  voiceNote("");
  const box = byId<HTMLTextAreaElement>("q");
  const text = box.value.trim();
  const hidden = box.dataset.attachText || "";
  const attachment = pendingDoc;
  if (!text && !hidden.trim()) return;
  if (sending) {
    queueDraft(text, hidden, attachment);
    return;
  }
  if (hidden || attachment) clearAttach();
  box.value = "";
  autoGrow(box);
  paintBrand();
  await sendText(text, false, false, { hidden, attachment });
}

function composerHasDraft(): boolean {
  const box = byId<HTMLTextAreaElement>("q");
  return Boolean((box.value || "").trim() || box.dataset.attachText);
}

function syncSend(): void {
  const go = byId<HTMLButtonElement>("go");
  const stop = byId<HTMLButtonElement>("stop");
  const kind = primaryKind(sending, composerHasDraft());
  go.classList.remove("voice", "send", "stop");
  go.classList.add(kind);
  go.disabled = uploading || attachError;
  go.setAttribute("aria-label", uploading ? "Uploading" : primaryLabel(kind));
  stop.hidden = !(sending && kind === "send");
  stop.disabled = uploading || attachError;
}

function autoGrow(box: HTMLTextAreaElement): void {
  box.style.height = "auto";
  box.style.height = Math.min(180, box.scrollHeight) + "px";
}

function clearAttach(): void {
  const box = byId<HTMLTextAreaElement>("q");
  delete box.dataset.attachText;
  pendingDoc = null;
  attachError = false;
  byId("fileTag").classList.remove("on", "err");
  byId<HTMLInputElement>("attach").value = "";
  syncSend();
}

function releaseAttachButton(): void {
  const button = byId<HTMLButtonElement>("btnAttach");
  button.classList.remove("live");
  button.disabled = false;
  button.removeAttribute("aria-busy");
}

function showUploadChip(label: string, failed = false): void {
  byId("fileName").textContent = label;
  const tag = byId("fileTag");
  tag.classList.add("on");
  tag.classList.toggle("err", failed);
  attachError = failed;
}

async function loadFile(file: File | null): Promise<void> {
  if (!file) return;
  const serial = ++attachSerial;
  const button = byId<HTMLButtonElement>("btnAttach");
  const current = () => serial === attachSerial;
  clearAttach();
  if (!current()) return;
  if (file.size <= 0) {
    voiceNote("That file is empty.");
    releaseAttachButton();
    return;
  }
  if (file.size > ATTACH_BYTES) {
    voiceNote(BIG_LINE);
    releaseAttachButton();
    return;
  }
  button.classList.add("live");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  uploading = true;
  syncSend();
  const label = file.name || "attachment";
  showUploadChip(label + " · 0%");
  voiceNote("");
  const body = new FormData();
  body.append("file", file, label);
  try {
    const response = await new Promise<{ ok: boolean; status: number; text: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/v1/attachments");
      xhr.upload.onprogress = (event) => {
        if (!current() || !event.lengthComputable || event.total <= 0) return;
        const pct = Math.min(100, Math.round((100 * event.loaded) / event.total));
        showUploadChip(label + " · " + pct + "%");
      };
      xhr.onload = () => {
        resolve({
          ok: xhr.status >= 200 && xhr.status < 300,
          status: xhr.status,
          text: xhr.responseText || "",
        });
      };
      xhr.onerror = () => reject(new Error("network"));
      xhr.onabort = () => reject(new Error("abort"));
      xhr.send(body);
    });
    let payload: AttachmentResult = {};
    try {
      payload = response.text ? (JSON.parse(response.text) as AttachmentResult) : {};
    } catch {
      if (current()) {
        showUploadChip(label + " · couldn't read it", true);
        voiceNote("");
        syncSend();
      }
      return;
    }
    if (!current()) return;
    if (!response.ok || !String(payload.text || "").trim()) {
      showUploadChip(label + " · couldn't read it", true);
      voiceNote("");
      syncSend();
      return;
    }
    const text = String(payload.text || "").trim();
    voiceNote("");
    byId<HTMLTextAreaElement>("q").dataset.attachText = text;
    pendingDoc = {
      name: label,
      route: payload.route === "ocr" ? "ocr" : "text",
      bytes: file.size,
      excerpt: docExcerpt(text),
    };
    const kb = Math.round((text.length / 1024) * 10) / 10;
    const via = payload.route === "ocr" ? "ocr" : "text";
    const cut = payload.truncated ? " · cut" : "";
    showUploadChip(label + " · " + via + cut + " (" + kb + " KB)");
  } catch {
    if (current()) {
      showUploadChip(label + " · couldn't read it", true);
      voiceNote("");
    }
  } finally {
    if (current()) {
      uploading = false;
      releaseAttachButton();
      syncSend();
    }
  }
}

function voiceNote(text: string): void {
  const note = byId("voiceNote");
  if (!text) {
    note.hidden = true;
    note.textContent = "";
    return;
  }
  note.hidden = false;
  note.textContent = text;
}

function paintVoice(): void {
  const mic = byId("btnVoice");
  paintMicButton(mic, dictating);
  mic.setAttribute("aria-pressed", dictating ? "true" : "false");
  mic.setAttribute("aria-label", "Voice");
  document.body.classList.toggle("voice-session", voiceOn);
  byId("voiceStage").setAttribute("aria-hidden", voiceOn ? "false" : "true");
  const tap = document.getElementById("voiceSend");
  if (tap) tap.hidden = !voiceOn;
}

function setVoiceThinking(on: boolean): void {
  byId("voiceStage").classList.toggle("thinking", on && voiceOn);
}

function setHeard(on: boolean): void {
  byId("voiceStage").classList.toggle("heard", on && voiceOn);
}

function setSpeaking(on: boolean): void {
  const stage = byId("voiceStage");
  stage.classList.toggle("speaking", on && voiceOn);
  if (on) setVoiceThinking(false);
  if (!on) stage.classList.remove("beat");
}

function pulseSpeaking(): void {
  const stage = byId("voiceStage");
  if (!stage.classList.contains("speaking")) return;
  stage.classList.remove("beat");
  void stage.offsetWidth;
  stage.classList.add("beat");
}

function voiceCaption(text: string): void {
  const line = byId("voiceLive");
  line.textContent = text;
  line.classList.toggle("idle", !text || text === "Listening");
}

function endListening(): void {
  listening = false;
  listenHandle = null;
  paintVoice();
}

function stopCapture(): void {
  listenHandle?.stop();
  listenHandle = null;
  listening = false;
}

function toggleVoice(): void {
  if (voiceOn || sending) return;
  if (dictating) {
    dictating = false;
    stopCapture();
    voiceNote("");
    paintVoice();
    return;
  }
  dictated = byId<HTMLTextAreaElement>("q").value.trim();
  dictating = true;
  voiceNote("");
  beginDictation();
}

function beginDictation(): void {
  if (!dictating || listening || voiceOn) return;
  const handle = startListening({
    onInterim(text) {
      const box = byId<HTMLTextAreaElement>("q");
      const lead = dictated.trim();
      const more = text.trim();
      box.value = lead && more ? lead + " " + more : (more || lead);
      autoGrow(box);
      syncSend();
    },
    onFinal(text) {
      const turn = turnFromRecognition(text);
      if (!turn) return;
      dictated = dictated ? dictated + " " + turn.content : turn.content;
      const box = byId<HTMLTextAreaElement>("q");
      box.value = dictated;
      autoGrow(box);
      syncSend();
    },
    onError() {
      dictating = false;
      endListening();
      voiceNote("Voice needs the microphone in this browser.");
    },
    onEnd() {
      const again = dictating && !voiceOn;
      endListening();
      if (again) beginDictation();
    },
  });
  if (!handle) {
    dictating = false;
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    paintVoice();
    return;
  }
  listening = true;
  listenHandle = handle;
  paintVoice();
}

function stopBarge(): void {
  bargeHandle?.stop();
  bargeHandle = null;
}

function takeBarge(text: string): void {
  const said = text.trim();
  if (!said || pendingBarge) return;
  pendingBarge = said;
  stopBarge();
  stopSpeaking();
  releaseVoice();
}

function armBarge(): void {
  if (bargeHandle || !voiceOn || listening) return;
  bargeHandle = startListening({
    onInterim(text) {
      if (shouldBargeIn(text, speakingLine, speechPending())) takeBarge(text);
    },
    onFinal(text) {
      if (shouldBargeIn(text, speakingLine, speechPending())) takeBarge(text);
    },
    onError() {
      stopBarge();
    },
    onEnd() {
      const again = voiceOn && !pendingBarge && speechPending();
      bargeHandle = null;
      if (again) armBarge();
    },
  });
}

function endVoiceMode(): void {
  voiceOn = false;
  voiceHold = false;
  pendingBarge = "";
  voiceUtterance = null;
  stopBarge();
  cancelUtterance?.();
  cancelUtterance = null;
  stopCapture();
  stopSpeaking();
  setHeard(false);
  setSpeaking(false);
  setVoiceThinking(false);
  voiceCaption("");
  voiceNote("");
  paintVoice();
}

function toggleVoiceMode(): void {
  if (voiceOn) {
    endVoiceMode();
    return;
  }
  if (sending) return;
  if (dictating) {
    dictating = false;
    stopCapture();
  }
  if (!speechReady()) {
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    paintVoice();
    return;
  }
  voiceOn = true;
  stopSpeaking();
  voiceNote("");
  voiceCaption("Listening");
  paintVoice();
  beginVoice();
}

function beginVoice(existing?: ReturnType<typeof createUtteranceHold>): void {
  if (!voiceOn || listening || voiceHold || sending) return;
  const hold = existing ?? createUtteranceHold((text) => {
    if (isSoloStop(text)) {
      endVoiceMode();
      return;
    }
    const turn = turnFromRecognition(text);
    voiceHold = Boolean(turn);
    const active = listenHandle;
    listenHandle = null;
    listening = false;
    active?.stop();
    setHeard(false);
    paintVoice();
    if (!turn) {
      voiceHold = false;
      if (voiceOn) beginVoice();
      return;
    }
    voiceCaption("Thinking");
    setVoiceThinking(true);
    void sendText(turn.content, false, true);
  }, endOfUtteranceSilence());
  voiceUtterance = hold;
  cancelUtterance = () => hold.cancel();
  const handle = startListening({
    onInterim(text) {
      const line = hold.interim(text);
      setHeard(Boolean(line));
      voiceCaption(line || "Listening");
    },
    onFinal(text) {
      const line = hold.final(text);
      if (isSoloStop(line)) {
        hold.cancel();
        endVoiceMode();
        return;
      }
      setHeard(Boolean(line));
      voiceCaption(line || "Listening");
    },
    onError() {
      hold.cancel();
      voiceOn = false;
      voiceHold = false;
      endListening();
      setHeard(false);
      voiceNote("Voice needs the microphone in this browser.");
    },
    onEnd() {
      const pendingLine = hold.text();
      const again = voiceOn && !voiceHold && !sending;
      endListening();
      if (!again) return;
      if (pendingLine) beginVoice(hold);
      else beginVoice();
    },
  }, { restart: true });
  if (!handle) {
    hold.cancel();
    cancelUtterance = null;
    voiceOn = false;
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    paintVoice();
    return;
  }
  listening = true;
  listenHandle = handle;
  const continued = hold.text();
  setHeard(Boolean(continued));
  voiceCaption(continued || "Listening");
  paintVoice();
}

function releaseVoice(): void {
  stopBarge();
  setVoiceThinking(false);
  if (speechPending()) return;
  const said = pendingBarge.trim();
  if (sending) {
    if (said) {
      stopAsked = true;
      turnCtrl?.abort();
    }
    return;
  }
  pendingBarge = "";
  voiceHold = false;
  setHeard(false);
  setSpeaking(false);
  if (said && isSoloStop(said)) {
    endVoiceMode();
    return;
  }
  if (said) {
    voiceCaption(said);
    void sendText(said, false, true);
    return;
  }
  if (!voiceOn || listening) return;
  voiceCaption("Listening");
  beginVoice();
}

byId("go").onclick = () => {
  if (uploading || attachError) return;
  if (sending && !composerHasDraft()) {
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
  if (uploading || attachError) return;
  stopCurrent();
};
const composer = byId<HTMLTextAreaElement>("q");
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
  if (uploading || attachError) {
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
  if (!enterToSend) return;
  event.preventDefault();
  void send();
});
function paintThinking(): void {
  document.querySelectorAll("[data-think]").forEach((node) => {
    node.classList.toggle("on", node.getAttribute("data-think") === thinking);
  });
}

function paintModelMode(): void {
  const label = modelMode === "flash" ? "Flash" : modelMode === "pro" ? "Pro" : "Auto";
  const name = document.getElementById("modeLabel");
  if (name) name.textContent = label;
  document.querySelectorAll("[data-mode]").forEach((node) => {
    const on = node.getAttribute("data-mode") === modelMode;
    node.classList.toggle("on", on);
    if (node.getAttribute("role") === "menuitemradio") node.setAttribute("aria-checked", on ? "true" : "false");
  });
}

function paintThemeChoice(): void {
  const theme = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  document.querySelectorAll("[data-theme-choice]").forEach((node) => {
    node.classList.toggle("on", node.getAttribute("data-theme-choice") === theme);
  });
}

function setThinking(next: string): void {
  thinking = next === "low" || next === "high" ? next : "medium";
  paintThinking();
  persistSettings();
}

function setModelMode(next: string): void {
  modelMode = next === "flash" || next === "pro" ? next : "auto";
  paintModelMode();
  closeInfoTips();
  const menu = document.getElementById("modePop");
  if (menu) menu.hidden = true;
  byId("modeBtn").setAttribute("aria-expanded", "false");
  persistSettings();
}

function setTheme(next: string): void {
  const theme: ThemeName = next === "light" ? "light" : "dark";
  applyTheme(theme);
  paintThemeChoice();
  persistSettings();
}

document.querySelectorAll("[data-think]").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = () => setThinking(btn.getAttribute("data-think") || "medium");
});
document.querySelectorAll("[data-mode]").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = (event) => {
    event.stopPropagation();
    setModelMode(btn.getAttribute("data-mode") || "auto");
  };
});
let pinnedInfo: HTMLElement | null = null;

function infoTip(btn: HTMLElement): HTMLElement | null {
  const id = btn.getAttribute("aria-describedby") || "";
  return id ? document.getElementById(id) : null;
}

function closeInfoTips(): void {
  pinnedInfo = null;
  document.querySelectorAll(".info-dot").forEach((node) => {
    node.setAttribute("aria-expanded", "false");
    const tip = infoTip(node as HTMLElement);
    if (tip) tip.hidden = true;
  });
}

function openInfo(btn: HTMLElement, pin: boolean): void {
  document.querySelectorAll(".info-dot").forEach((node) => {
    if (node === btn) return;
    node.setAttribute("aria-expanded", "false");
    const other = infoTip(node as HTMLElement);
    if (other) other.hidden = true;
  });
  if (pinnedInfo && pinnedInfo !== btn) pinnedInfo = null;
  const tip = infoTip(btn);
  if (!tip) return;
  btn.setAttribute("aria-expanded", "true");
  tip.hidden = false;
  if (pin) pinnedInfo = btn;
}

document.querySelectorAll(".info-slot").forEach((slot) => {
  const btn = slot.querySelector(".info-dot") as HTMLButtonElement | null;
  if (!btn) return;
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (pinnedInfo === btn) {
      pinnedInfo = null;
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
    if (pinnedInfo === btn) return;
    btn.setAttribute("aria-expanded", "false");
    const tip = infoTip(btn);
    if (tip) tip.hidden = true;
  });
});
function closeModeMenu(): void {
  const menu = document.getElementById("modePop");
  if (menu) menu.hidden = true;
  byId("modeBtn").setAttribute("aria-expanded", "false");
}

function dismissPopovers(event?: Event): void {
  const target = event?.target as { closest?: (selector: string) => unknown } | null;
  if (target && typeof target.closest === "function" && target.closest(".info-slot, #modePop, #modeBtn")) {
    return;
  }
  closeInfoTips();
  closeModeMenu();
}

function newChat(): void {
  clearFollowQueue();
  chatEpoch += 1;
  stopAsked = true;
  turnCtrl?.abort();
  sending = false;
  turns.length = 0;
  const box = byId<HTMLTextAreaElement>("q");
  box.value = "";
  autoGrow(box);
  clearAttach();
  voiceNote("");
  setSourcesOpen(false);
  setSettingsOpen(false);
  closeInfoTips();
  closeModeMenu();
  paint();
  paintBrand();
  box.focus();
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  dismissPopovers();
  setSettingsOpen(false);
  setSourcesOpen(false);
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
enterBox.checked = enterToSend;
enterBox.addEventListener("change", () => {
  enterToSend = enterBox.checked;
  persistSettings();
});
applyTheme(pageSettings.theme);
applyVoiceSilence(pageSettings.voiceSilenceMs);
paintThinking();
paintModelMode();
paintThemeChoice();
whenSpeechStarts((text) => {
  if (!voiceOn) return;
  speakingLine = text || speakingLine;
  setHeard(false);
  setSpeaking(true);
  voiceCaption("Speaking");
  armBarge();
});
whenSpeechPulses(() => {
  if (!voiceOn) return;
  pulseSpeaking();
});
whenSpeechEnds(releaseVoice);
byId("btnVoice").onclick = () => toggleVoice();
const voiceSend = document.getElementById("voiceSend");
if (voiceSend) voiceSend.onclick = () => voiceUtterance?.flush();
byId("btnNew").onclick = () => newChat();
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
byId("fileClear").onclick = () => clearAttach();
function paintBrand(typing?: boolean): void {
  const brand = document.getElementById("brand");
  const box = document.getElementById("q") as HTMLTextAreaElement | null;
  if (!brand || !box) return;
  const on = typeof typing === "boolean" ? typing : Boolean(box.value);
  brand.classList.toggle("brand-title", on);
  brand.classList.toggle("brand-logo", !on);
}

function bootSplash(): void {
  const mark = document.getElementById("brandMark");
  if (mark) appendBrandMark(document, mark);
  paintBrand();
  const store = browserStorage("session");
  const seen = store ? store.getItem(SPLASH_KEY) : null;
  if (!shouldPlaySplash(seen, navigationType())) return;
  if (store) store.setItem(SPLASH_KEY, "1");
  const brand = document.getElementById("brand");
  if (!brand) return;
  brand.classList.add("brand-enter");
  window.setTimeout(() => brand.classList.remove("brand-enter"), SPLASH_HOLD_MS);
}
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

void refresh();
window.setInterval(() => {
  if (!shouldPollHealth(document.hidden)) return;
  void refresh();
}, HEALTH_POLL_MS);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  resumedAt = Date.now();
  window.setTimeout(() => {
    if (!shouldPollHealth(document.hidden)) return;
    const resume = resumeSend;
    resumeSend = null;
    if (resume) resume();
    void refresh();
  }, VISIBILITY_SETTLE_MS);
});
