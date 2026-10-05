import { docExcerpt, modelUserContent, type DocCard } from "./attach";
import { failChart, drawChart } from "./chart";
import { cardsFrom, renderImageCardsHtml, type ImageCard } from "./images";
import { renderMarkdown, renderStreamingMarkdown } from "./markdown";
import { paintMicButton } from "./mic-button";
import { suppressOfflineBanner } from "./presence";
import { primaryKind, primaryLabel } from "./primary-action";
import { applyTheme, applyVoiceSilence, browserStorage, loadSettings, saveSettings, type ModelMode, type PageSettings, type ThinkLevel, type ThemeName } from "./settings";
import { renderFailedSearch, renderSourcesPanelBody, renderSourcesPill, type PanelDetail, type SourceLink as PillSource } from "./sources";
import { appendBrandMark, navigationType, shouldPlaySplash, SPLASH_HOLD_MS, SPLASH_KEY } from "./splash";
import { createUtteranceHold, endOfUtteranceSilence, isSoloStop, noteSpokenDelta, shouldBargeIn, speakText, speechPending, speechReady, startListening, stopSpeaking, turnFromRecognition, whenSpeechEnds, whenSpeechPulses, whenSpeechStarts } from "./voice";

declare global {
  interface Window {
    MESH_DEFAULT_MODEL?: string;
    Plotly?: {
      newPlot: (
        node: HTMLElement,
        data: Record<string, unknown>[],
        layout: Record<string, unknown>,
        config: Record<string, unknown>,
      ) => void | Promise<unknown>;
    };
  }
}

type Role = "user" | "assistant";
type StageName = "loading" | "thinking" | "searching" | "answering";

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
  images?: ImageCard[];
  mode?: string;
  route?: string;
}

interface HealthBody {
  peers?: { models?: string[] }[];
  peers_up?: number;
  uptime_s?: number;
  services?: {
    brain?: { ok?: boolean; latency_ms?: number | null };
    search?: { ok?: boolean; latency_ms?: number | null };
    peers_up?: number;
    peers?: number;
  };
}

interface LiveTurn {
  root: HTMLElement;
  body: HTMLElement;
  stagesEl: HTMLElement;
  seen: StageName[];
  search: SearchInfo | null;
  pushStatus: (name: StageName, search: SearchInfo | null) => void;
  setText: (text: string) => void;
  showImages: (cards: ImageCard[]) => void;
  finish: (text: string, failed: boolean, prompt: string, effort: string, search: SearchInfo | null, stages: StageName[]) => void;
  markErr: () => void;
  armPro: () => void;
}

const DEFAULT_MODEL = window.MESH_DEFAULT_MODEL || "qwen2.5:0.5b";
const turns: Turn[] = [];
let sending = false;
let stopAsked = false;
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
let bargeHandle: { stop: () => void } | null = null;
let pendingBarge = "";
let speakingLine = "";
const serviceLines: string[] = [];
let attachSerial = 0;

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
  if (modelMode === "pro") return "qwen2.5:1.5b";
  if (modelMode === "flash") return "qwen2.5:0.5b";
  return DEFAULT_MODEL;
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

const PLOTLY_SRC = "/static/plotly.min.js";
let plotlyLoader: Promise<NonNullable<Window["Plotly"]> | null> | null = null;

function loadPlotly(): Promise<NonNullable<Window["Plotly"]> | null> {
  if (window.Plotly?.newPlot) return Promise.resolve(window.Plotly);
  if (!plotlyLoader) {
    plotlyLoader = new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = PLOTLY_SRC;
      script.async = true;
      script.onload = () => resolve(window.Plotly?.newPlot ? window.Plotly : null);
      script.onerror = () => {
        plotlyLoader = null;
        resolve(null);
      };
      document.head.appendChild(script);
    });
  }
  return plotlyLoader;
}

function mountCharts(root: ParentNode): void {
  const nodes = [...root.querySelectorAll(".pi-chart")].filter(
    (node): node is HTMLElement => node instanceof HTMLElement,
  );
  if (!nodes.length) return;
  void loadPlotly().then((api) => {
    nodes.forEach((node) => {
      if (!node.isConnected) return;
      if (!api || !drawChart(node, api.newPlot.bind(api))) failChart(node);
    });
  });
}

function setBodyContent(node: HTMLElement, text: string, asMd: boolean, streaming = false): void {
  if (asMd) {
    node.classList.add("md");
    node.innerHTML = streaming ? renderStreamingMarkdown(text) : renderMarkdown(text);
    mountCharts(node);
  } else {
    node.classList.remove("md");
    node.textContent = text;
  }
}

function effortLabel(name: string): string {
  const key = String(name || "").toLowerCase();
  if (key === "low") return "Low";
  if (key === "medium") return "Medium";
  if (key === "high") return "High";
  return "";
}

function showEffort(parent: HTMLElement, name: string): void {
  const label = effortLabel(name);
  if (!label) return;
  const node = el("span", "effort", label);
  const labels = parent.querySelector(".label-row");
  if (labels) labels.insertBefore(node, labels.firstChild);
  else parent.appendChild(node);
}

function modeChipText(mode: string, route: string): string {
  const tier = route === "flash" ? "Flash" : route === "pro" ? "Pro" : route === "canned" ? "map" : "";
  if (mode === "auto") return tier ? "Auto · " + tier : "Auto";
  if (mode === "flash") return route === "canned" ? "Flash · map" : "Flash";
  if (mode === "pro") return route === "canned" ? "Pro · map" : "Pro";
  return "";
}

function showMode(parent: HTMLElement, mode: string, route: string): void {
  const label = modeChipText(mode, route);
  if (!label) return;
  const node = el("span", "mode-chip", label);
  const labels = parent.querySelector(".label-row");
  if (labels) labels.insertBefore(node, labels.firstChild);
  else parent.appendChild(node);
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

function mountImageCards(row: HTMLElement, before: Node | null, raw: unknown): void {
  row.querySelector(".image-cards")?.remove();
  const html = renderImageCardsHtml(raw);
  if (!html) return;
  const holder = document.createElement("div");
  holder.innerHTML = html;
  const strip = holder.firstElementChild as HTMLElement | null;
  if (!strip) return;
  strip.querySelectorAll("img").forEach((node) => {
    node.addEventListener("error", () => {
      node.closest(".image-card")?.remove();
      if (!strip.querySelector(".image-card")) strip.remove();
    });
  });
  if (before && before.parentNode === row) row.insertBefore(strip, before);
  else row.appendChild(strip);
}

function stageText(name: StageName, search: SearchInfo | null): string {
  if (name === "loading") return "Loading Pro";
  if (name === "thinking") return "Thinking";
  if (name === "searching") {
    if (search?.status === "failed") return "Search failed";
    if (search?.status === "ok") return "Searched";
    return "Searching";
  }
  return "Answering";
}

function shownError(err: unknown): string {
  const text = String((err as { message?: string })?.message || err || "");
  if (/Load failed|Failed to fetch|NetworkError|network|abort|AbortError/i.test(text)) {
    return "Connection dropped. Try again.";
  }
  if (/at capacity/i.test(text)) return text;
  return "The reply did not come back. Try again.";
}

function visibleReply(text: string): boolean {
  return text.trim().length > 0;
}

function fillModels(rows: { models?: string[] }[]): void {
  const select = document.getElementById("modelSel") as HTMLSelectElement | null;
  if (!select) return;
  const prev = select.value || DEFAULT_MODEL;
  const set = new Set<string>([DEFAULT_MODEL]);
  (rows || []).forEach((row) => {
    (row.models || []).forEach((model) => {
      if (model) set.add(model);
    });
  });
  const list = [...set].sort((a, b) => {
    if (a === DEFAULT_MODEL) return -1;
    if (b === DEFAULT_MODEL) return 1;
    return a.localeCompare(b);
  });
  select.replaceChildren();
  list.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    select.appendChild(option);
  });
  select.value = list.includes(prev) ? prev : DEFAULT_MODEL;
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
  if (suppressOfflineBanner(document.hidden, resumedAt, Date.now())) return;
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

async function refresh(): Promise<void> {
  try {
    const response = await fetch("/health", { method: "GET", cache: "no-store" });
    if (!response.ok) throw new Error("offline");
    const body = await response.json() as HealthBody;
    const banner = byId("banner");
    banner.className = "";
    banner.replaceChildren();
    fillModels(body.peers || []);
    paintServices(body);
  } catch {
    showOffline();
  }
}

function formatUptime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours) return hours + "h " + minutes + "m";
  if (minutes) return minutes + "m " + (total % 60) + "s";
  return total + "s";
}

function paintServices(body: HealthBody): void {
  const now = document.getElementById("serviceNow");
  const log = document.getElementById("serviceLog");
  if (!now || !log) return;
  const services = body.services || {};
  const chat = services.brain?.ok ? "up" : "down";
  const search = services.search?.ok ? "up" : "down";
  const up = services.peers_up ?? body.peers_up ?? 0;
  const total = services.peers ?? (body.peers || []).length;
  const line = "Up " + formatUptime(body.uptime_s || 0) + " · Chat " + chat + " · Search " + search + " · Fleet " + up + "/" + total;
  now.textContent = line;
  if (serviceLines[0] !== line) {
    serviceLines.unshift(line);
    if (serviceLines.length > 8) serviceLines.length = 8;
  }
  log.replaceChildren();
  serviceLines.forEach((entry) => log.appendChild(el("p", "service-line", entry)));
}

async function backgroundMiss(): Promise<boolean> {
  if (suppressOfflineBanner(document.hidden, resumedAt, Date.now())) return true;
  await new Promise((resolve) => window.setTimeout(resolve, 350));
  return suppressOfflineBanner(document.hidden, resumedAt, Date.now());
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
  if (item?.attachment) row.appendChild(docCardNode(item.attachment));
  if (text.trim()) {
    const body = el("div", "body");
    body.textContent = text;
    row.appendChild(body);
  }
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
  if (visibleReply(item.content)) {
    body = el("div", "body");
    setBodyContent(body, item.content, true);
    row.appendChild(body);
  }
  if (item.images?.length) mountImageCards(row, body, item.images);
  const asked = promptBefore(index);
  if (item.search) showSearch(row, item.search.status, item.search.sources, item.stages || [], asked);
  if (asked) attachLabel(row, asked, item.content);
  showEffort(row, item.effort || "");
  showMode(row, item.mode || "", item.route || "");
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
    stagesEl.setAttribute("aria-label", "Loading Pro");
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
  const queued: StageName[] = [];
  let holding = false;

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
    if (holdPro && !visibleReply(body.textContent || "")) return "Loading Pro";
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
    pushStatus(name, search) {
      if (search && (search.status === "ok" || search.status === "failed")) live.search = search;
      if (!live.seen.includes(name)) live.seen.push(name);
      enqueue(name);
      row.scrollIntoView({ block: "end" });
    },
    setText(text) {
      if (!visibleReply(text)) return;
      setBodyContent(body, text, true, true);
      revealReply(text);
      const current = viewport.querySelector(".stage:not(.leave)") as HTMLElement | null;
      if (current && shown) stageLabel(current, labelFor(shown));
      yieldIfAnswer();
      row.scrollIntoView({ block: "end" });
    },
    showImages(cards) {
      mountImageCards(row, body, cards);
    },
    finish(text, failed, prompt, effort, search, stages) {
      row.classList.remove("streaming");
      if (visibleReply(text)) {
        setBodyContent(body, text, !failed);
        revealReply(text);
      }
      if (!failed && search) showSearch(row, search.status, search.sources, stages, prompt);
      if (prompt && !failed) attachLabel(row, prompt, text);
      if (!failed) showEffort(row, effort);
      const done = trail(stages, search);
      if (done && !failed) row.insertBefore(done, stagesEl);
      stagesEl.remove();
      row.scrollIntoView({ block: "end", behavior: "smooth" });
    },
    markErr() {
      row.classList.add("err");
      row.classList.remove("streaming");
      stagesEl.removeAttribute("aria-busy");
      row.querySelector(".image-cards")?.remove();
    },
    armPro() {
      if (holdPro) return;
      holdPro = true;
      stagesEl.classList.add("pro-load");
      stagesEl.setAttribute("aria-busy", "true");
      stagesEl.setAttribute("aria-label", "Loading Pro");
      enqueue("loading");
    },
  };
  if (holdPro) enqueue("loading");
  return live;
}

function keepPartial(
  live: LiveTurn | null,
  text: string,
  prompt: string,
  effort: string,
  search: SearchInfo | null,
  stages: StageName[],
  images: ImageCard[],
  mode = "",
  route = "",
): void {
  if (visibleReply(text)) {
    turns.push({ role: "assistant", content: text, effort, search, stages, images, mode, route });
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

async function sendText(
  text: string,
  isRetry: boolean,
  spoken = false,
  extra?: { hidden?: string; attachment?: DocCard | null },
): Promise<void> {
  if (sending) return;
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
  const model = modeModel();
  const effort = thinking || "medium";
  const live = addLiveBot(modelMode === "pro");
  let textAccum = "";
  let searchStatus = "";
  let searchSources: SourceLink[] = [];
  let imageCards: ImageCard[] = [];
  const stages: StageName[] = [];
  const noteImages = (raw: unknown) => {
    const next = cardsFrom(raw);
    if (!next.length) return;
    imageCards = next;
    live.showImages(imageCards);
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
      const content = turn.role === "user" ? modelUserContent(turn.content, turn.hidden || "") : turn.content;
      if (!content.trim()) return;
      body.messages.push({ role: turn.role, content });
    });

    let response: Response | null = null;
    let lastErr: unknown = null;
    for (let i = 0; i <= 2; i += 1) {
      if (stopAsked) break;
      try {
        turnCtrl = new AbortController();
        const timer = window.setTimeout(() => turnCtrl?.abort(), 180000);
        response = await fetch("/v1/chat/completions", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "X-Pi-Target": "auto",
            "X-Pi-Mesh": "on",
            "X-Pi-Mode": modelMode,
          },
          body: JSON.stringify(body),
          signal: turnCtrl.signal,
          cache: "no-store",
        });
        window.clearTimeout(timer);
        lastErr = null;
        break;
      } catch (err) {
        if (stopAsked) throw err;
        lastErr = err;
        if (i < 2) await new Promise((resolve) => window.setTimeout(resolve, 600 * (i + 1)));
      }
    }
    const searchNow = (): SearchInfo | null => (
      searchStatus ? { status: searchStatus, sources: searchSources } : null
    );
    if (stopAsked) {
      keepPartial(live, textAccum, text, effort, searchNow(), stages, imageCards, modelMode, "");
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
        pi_images?: unknown;
        pi_stages?: StageName[];
        choices?: { message?: { content?: string } }[];
      } = {};
      try {
        payload = textBody ? JSON.parse(textBody) : {};
      } catch {
        payload = {};
      }
      if (!response.ok) {
        const msg = shownError(payload.error || "HTTP " + response.status);
        live.setText(msg);
        live.markErr();
        live.finish(msg, true, "", "", null, stages);
        const retry = el("button", "retry", "Retry");
        retry.onclick = () => {
          live.root.remove();
          void sendText(text, true);
        };
        live.root.appendChild(retry);
        return;
      }
      const answer = payload.choices?.[0]?.message?.content || "";
      const search = searchFrom(payload, searchNow());
      const doneStages = Array.isArray(payload.pi_stages) ? payload.pi_stages : stages;
      noteImages(payload.pi_images);
      if (!visibleReply(answer)) {
        live.root.remove();
        return;
      }
      turns.push({
        role: "assistant",
        content: answer,
        effort: payload.pi_think || streamedEffort,
        search,
        stages: doneStages,
        images: imageCards,
        mode: payload.pi_mode || streamedMode,
        route: payload.pi_route || streamedRoute,
      });
      paint();
      if (spoken && speakText(answer)) voiced = true;
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("no stream");
    const decoder = new TextDecoder();
    let buf = "";
    let streamDone = false;
    let streamErr = "";
    while (!streamDone) {
      if (stopAsked) break;
      const chunk = await reader.read();
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
          pi_images?: unknown;
          pi_stages?: StageName[];
          choices?: { delta?: { content?: string } }[];
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
        if (Array.isArray(payload.pi_images)) noteImages(payload.pi_images);
        if (payload.pi_status) {
          live.pushStatus(payload.pi_status, searchNow());
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
        const delta = payload.choices?.[0]?.delta?.content;
        if (delta) {
          textAccum += delta;
          live.setText(textAccum);
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
    if (stopAsked) {
      keepPartial(live, textAccum, text, streamedEffort || effort, searchNow(), stages, imageCards, streamedMode, streamedRoute);
      return;
    }
    if (streamErr || (!response.ok && !visibleReply(textAccum))) {
      const msg = shownError(streamErr || "HTTP " + response.status);
      live.setText(msg);
      live.markErr();
      live.finish(msg, true, "", "", null, stages);
      live.root.appendChild(retryButton(() => {
        live.root.remove();
        void sendText(text, true);
      }));
      return;
    }
    if (!visibleReply(textAccum)) {
      live.root.remove();
      return;
    }
    const search = searchNow();
    turns.push({
      role: "assistant",
      content: textAccum,
      effort: streamedEffort,
      search,
      stages,
      images: imageCards,
      mode: streamedMode,
      route: streamedRoute,
    });
    paint();
    if (spoken && speakText(textAccum)) voiced = true;
  } catch (err) {
    if (stopAsked) {
      keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, imageCards, modelMode, "");
      return;
    }
    if (await backgroundMiss()) {
      if (visibleReply(textAccum)) {
        keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages, imageCards, modelMode, "");
      } else {
        live.root.remove();
      }
      return;
    }
    const msg = shownError(err);
    live.setText(msg);
    live.markErr();
    live.finish(msg, true, "", "", null, stages);
    live.root.appendChild(retryButton(() => {
      live.root.remove();
      void sendText(text, true);
    }));
  } finally {
    sending = false;
    stopAsked = false;
    turnCtrl = null;
    syncSend();
    byId<HTMLTextAreaElement>("q").focus();
    if (voiceOn && !speechPending()) releaseVoice();
  }
}

async function send(): Promise<void> {
  voiceNote("");
  const box = byId<HTMLTextAreaElement>("q");
  const text = box.value.trim();
  const hidden = box.dataset.attachText || "";
  const attachment = pendingDoc;
  if (!text && !hidden.trim()) return;
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
  const kind = primaryKind(sending, composerHasDraft());
  go.classList.remove("voice", "send", "stop");
  go.classList.add(kind);
  go.disabled = false;
  go.setAttribute("aria-label", primaryLabel(kind));
}

function autoGrow(box: HTMLTextAreaElement): void {
  box.style.height = "auto";
  box.style.height = Math.min(180, box.scrollHeight) + "px";
}

function threadAsMd(): string {
  let out = "# OpenPi — MicroAstra\n\n";
  turns.forEach((turn) => {
    out += "### " + (turn.role === "user" ? "You" : "Assistant") + "\n\n" + turn.content + "\n\n";
  });
  return out;
}

function threadAsTxt(): string {
  return turns.map((turn) => {
    const who = turn.role === "user" ? "You" : "Assistant";
    return who + ":\n" + turn.content;
  }).join("\n\n---\n\n");
}

function download(name: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: mime });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 2000);
}

function clearAttach(): void {
  const box = byId<HTMLTextAreaElement>("q");
  delete box.dataset.attachText;
  pendingDoc = null;
  byId("fileTag").classList.remove("on");
  byId<HTMLInputElement>("attach").value = "";
  syncSend();
}

function releaseAttachButton(): void {
  const button = byId<HTMLButtonElement>("btnAttach");
  button.classList.remove("live");
  button.disabled = false;
  button.removeAttribute("aria-busy");
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
    voiceNote("That file is over 4 MB.");
    releaseAttachButton();
    return;
  }
  button.classList.add("live");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  voiceNote("Reading " + (file.name || "file") + "…");
  const body = new FormData();
  body.append("file", file, file.name || "attachment");
  try {
    const response = await fetch("/v1/attachments", { method: "POST", body });
    let payload: AttachmentResult = {};
    try {
      payload = (await response.json()) as AttachmentResult;
    } catch {
      if (current()) voiceNote("Could not read that file.");
      return;
    }
    if (!current()) return;
    if (!response.ok) {
      voiceNote(payload.error || "Could not read that file.");
      return;
    }
    const text = String(payload.text || "").trim();
    if (!text) {
      voiceNote("No text in that file.");
      return;
    }
    voiceNote("");
    byId<HTMLTextAreaElement>("q").dataset.attachText = text;
    pendingDoc = {
      name: file.name || "attachment",
      route: payload.route === "ocr" ? "ocr" : "text",
      bytes: file.size,
      excerpt: docExcerpt(text),
    };
    const kb = Math.round((text.length / 1024) * 10) / 10;
    const via = payload.route === "ocr" ? "ocr" : "text";
    const cut = payload.truncated ? " · cut" : "";
    byId("fileName").textContent = (file.name || "attachment") + " · " + via + cut + " (" + kb + " KB)";
    byId("fileTag").classList.add("on");
    syncSend();
  } catch {
    if (current()) voiceNote("Could not read that file.");
  } finally {
    if (current()) releaseAttachButton();
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
}

function setHeard(on: boolean): void {
  byId("voiceStage").classList.toggle("heard", on && voiceOn);
}

function setSpeaking(on: boolean): void {
  const stage = byId("voiceStage");
  stage.classList.toggle("speaking", on && voiceOn);
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
  if (!said || pendingBarge || sending) return;
  pendingBarge = said;
  stopBarge();
  stopSpeaking();
}

function armBarge(): void {
  if (bargeHandle || !voiceOn || listening) return;
  bargeHandle = startListening({
    onInterim(text) {
      if (shouldBargeIn(text, speakingLine, true)) takeBarge(text);
    },
    onFinal(text) {
      if (shouldBargeIn(text, speakingLine, true)) takeBarge(text);
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
  stopBarge();
  cancelUtterance?.();
  cancelUtterance = null;
  stopCapture();
  stopSpeaking();
  setHeard(false);
  setSpeaking(false);
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
    voiceCaption(turn.content);
    void sendText(turn.content, false, true);
  }, endOfUtteranceSilence());
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
  });
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
  if (speechPending() || sending) return;
  const said = pendingBarge.trim();
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
  if (sending) {
    stopAsked = true;
    stopSpeaking();
    turnCtrl?.abort();
    return;
  }
  if (!composerHasDraft()) {
    toggleVoiceMode();
    return;
  }
  void send();
};
const composer = byId<HTMLTextAreaElement>("q");
composer.addEventListener("input", () => {
  autoGrow(composer);
  syncSend();
  paintBrand();
});
syncSend();
composer.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  if (event.shiftKey) return;
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
document.querySelectorAll("[data-theme-choice]").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = () => setTheme(btn.getAttribute("data-theme-choice") || "dark");
});
byId("modeBtn").onclick = (event) => {
  event.stopPropagation();
  const menu = byId("modePop");
  const open = menu.hidden;
  menu.hidden = !open;
  byId("modeBtn").setAttribute("aria-expanded", open ? "true" : "false");
};
document.addEventListener("click", () => {
  const menu = document.getElementById("modePop");
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  byId("modeBtn").setAttribute("aria-expanded", "false");
});
const silenceInput = byId<HTMLInputElement>("voiceSilence");
silenceInput.value = String(pageSettings.voiceSilenceMs);
silenceInput.addEventListener("change", () => {
  const next = Number(silenceInput.value);
  applyVoiceSilence(next);
  silenceInput.value = String(endOfUtteranceSilence());
  persistSettings();
});
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
byId("btnIo").onclick = () => setSettingsOpen(true);
byId("btnCloseIo").onclick = () => setSettingsOpen(false);
byId("overlay").onclick = () => {
  setSettingsOpen(false);
  setSourcesOpen(false);
};
byId("btnClear").onclick = () => {
  if (typeof window.confirm === "function" && !window.confirm("Clear this conversation?")) return;
  turns.length = 0;
  stopSpeaking();
  paint();
  setSettingsOpen(false);
};
byId("sourcesClose").onclick = () => setSourcesOpen(false);
byId("btnMd").onclick = () => {
  if (!turns.length) return;
  download("chat.md", threadAsMd(), "text/markdown");
};
byId("btnTxt").onclick = () => {
  if (!turns.length) return;
  download("chat.txt", threadAsTxt(), "text/plain");
};
byId("btnAttach").onclick = () => byId<HTMLInputElement>("attach").click();
byId<HTMLInputElement>("attach").onchange = (event) => {
  const input = event.target as HTMLInputElement;
  loadFile(input.files && input.files[0]);
};
byId("fileClear").onclick = () => clearAttach();
function paintBrand(): void {
  const brand = document.getElementById("brand");
  if (!brand) return;
  const typing = Boolean(byId<HTMLTextAreaElement>("q").value);
  brand.classList.toggle("brand-title", typing);
  brand.classList.toggle("brand-logo", !typing);
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
window.setInterval(() => void refresh(), 8000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  resumedAt = Date.now();
  window.setTimeout(() => {
    if (!document.hidden) void refresh();
  }, 500);
});
