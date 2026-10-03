import { renderMarkdown } from "./markdown";
import { speakText, speechReady, startListening, stopSpeaking } from "./voice";

declare global {
  interface Window {
    MESH_DEFAULT_MODEL?: string;
  }
}

type Role = "user" | "assistant";
type StageName = "thinking" | "searching" | "answering";

interface SourceLink {
  title: string;
  url: string;
}

interface SearchInfo {
  status: string;
  sources: SourceLink[];
}

interface Turn {
  role: Role;
  content: string;
  effort?: string;
  search?: SearchInfo | null;
  stages?: StageName[];
}

interface LiveTurn {
  root: HTMLElement;
  body: HTMLElement;
  stagesEl: HTMLElement;
  seen: StageName[];
  search: SearchInfo | null;
  pushStatus: (name: StageName, search: SearchInfo | null) => void;
  setText: (text: string) => void;
  finish: (text: string, failed: boolean, prompt: string, effort: string, search: SearchInfo | null, stages: StageName[]) => void;
  markErr: () => void;
}

const DEFAULT_MODEL = window.MESH_DEFAULT_MODEL || "qwen2.5:0.5b";
const turns: Turn[] = [];
let sending = false;
let stopAsked = false;
let turnCtrl: AbortController | null = null;
let thinking = "medium";
let listening = false;
let speakThisReply = false;
let listenHandle: { stop: () => void } | null = null;

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

function currentModel(): string {
  const select = document.getElementById("modelSel") as HTMLSelectElement | null;
  return (select && select.value) || DEFAULT_MODEL;
}

function hideEmpty(): void {
  document.getElementById("empty")?.remove();
}

function setSettingsOpen(on: boolean): void {
  byId("ioPanel").classList.toggle("open", on);
  byId("overlay").classList.toggle("open", on);
}

function setBodyContent(node: HTMLElement, text: string, asMd: boolean): void {
  if (asMd) {
    node.classList.add("md");
    node.innerHTML = renderMarkdown(text);
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

function showSearch(parent: HTMLElement, status: string, sources: SourceLink[]): void {
  if (status !== "ok" && status !== "failed") return;
  const note = el("div", "search-note");
  if (status === "failed") {
    note.textContent = "Search failed";
  } else {
    note.appendChild(el("span", "search-kicker", "Searched"));
    sources.slice(0, 3).forEach((src) => {
      const url = src && src.url ? String(src.url) : "";
      if (!url.startsWith("http")) return;
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = src.title || url;
      note.appendChild(link);
    });
  }
  parent.appendChild(note);
}

function stageText(name: StageName, search: SearchInfo | null): string {
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
  return "The reply did not come back. Try again.";
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
    const body = await response.json() as { peers?: { models?: string[] }[] };
    const banner = byId("banner");
    banner.className = "";
    banner.replaceChildren();
    fillModels(body.peers || []);
  } catch {
    showOffline();
  }
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

function addUser(text: string, index: number): HTMLElement {
  hideEmpty();
  const row = el("div", "msg user");
  row.dataset.index = String(index);
  const body = el("div", "body");
  body.textContent = text;
  row.appendChild(body);
  const acts = el("div", "msg-actions");
  acts.appendChild(copyButton(text));
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
    if (!next) return;
    turns.splice(index);
    void sendText(next, false);
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
  const body = el("div", "body");
  setBodyContent(body, item.content, true);
  row.appendChild(body);
  if (item.search) showSearch(row, item.search.status, item.search.sources);
  const asked = promptBefore(index);
  if (asked) attachLabel(row, asked, item.content);
  showEffort(row, item.effort || "");
  let labels = row.querySelector(".label-row");
  if (!labels) {
    labels = el("div", "label-row");
    row.appendChild(labels);
  }
  labels.appendChild(copyButton(item.content));
  if (index === turns.length - 1) {
    const again = el("button", "text-btn", "Regenerate");
    again.setAttribute("aria-label", "Regenerate");
    again.onclick = () => regenerate();
    labels.appendChild(again);
  }
  byId("log").appendChild(row);
  return row;
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

function syncStageNodes(live: { stagesEl: HTMLElement; seen: StageName[]; search: SearchInfo | null; body: HTMLElement }): void {
  const hideAnswer = live.body.textContent !== "" && !live.body.querySelector(".pending");
  const visible = live.seen.filter((name) => !(hideAnswer && name === "answering"));
  visible.forEach((name) => {
    let node = live.stagesEl.querySelector('[data-stage="' + name + '"]') as HTMLElement | null;
    if (!node) {
      node = el("div", "stage");
      node.dataset.stage = name;
      node.appendChild(el("span", "stage-dot"));
      node.appendChild(el("span", "stage-label"));
      live.stagesEl.appendChild(node);
    }
    const label = node.querySelector(".stage-label");
    if (label) label.textContent = stageText(name, live.search);
  });
  live.stagesEl.querySelectorAll(".stage").forEach((node) => {
    const name = (node as HTMLElement).dataset.stage || "";
    if (!visible.includes(name as StageName)) {
      node.remove();
      return;
    }
    const current = visible[visible.length - 1];
    node.classList.toggle("on", name === current);
    node.classList.toggle("done", name !== current);
  });
}

function addLiveBot(): LiveTurn {
  hideEmpty();
  const row = el("div", "msg bot streaming");
  const stagesEl = el("div", "stages");
  stagesEl.setAttribute("aria-live", "polite");
  const body = el("div", "body");
  const dots = el("span", "pending");
  dots.appendChild(el("i"));
  dots.appendChild(el("i"));
  dots.appendChild(el("i"));
  body.appendChild(dots);
  row.appendChild(stagesEl);
  row.appendChild(body);
  byId("log").appendChild(row);
  row.scrollIntoView({ block: "end" });
  const live: LiveTurn = {
    root: row,
    body,
    stagesEl,
    seen: [],
    search: null,
    pushStatus(name, search) {
      if (search && (search.status === "ok" || search.status === "failed")) live.search = search;
      if (!live.seen.includes(name)) live.seen.push(name);
      const pending = body.querySelector(".pending");
      if (pending) pending.remove();
      syncStageNodes(live);
      row.scrollIntoView({ block: "end" });
    },
    setText(text) {
      body.classList.remove("md");
      body.textContent = text;
      syncStageNodes(live);
      row.scrollIntoView({ block: "end" });
    },
    finish(text, failed, prompt, effort, search, stages) {
      row.classList.remove("streaming");
      setBodyContent(body, text, !failed);
      if (!failed && search) showSearch(row, search.status, search.sources);
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
    },
  };
  return live;
}

function keepPartial(live: LiveTurn | null, text: string, prompt: string, effort: string, search: SearchInfo | null, stages: StageName[]): void {
  if (text) {
    turns.push({ role: "assistant", content: text, effort, search, stages });
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

async function sendText(text: string, isRetry: boolean, spoken = false): Promise<void> {
  if (sending) return;
  sending = true;
  stopAsked = false;
  stopSpeaking();
  speakThisReply = false;
  syncSend();
  if (!isRetry) {
    turns.push({ role: "user", content: text });
    paint();
  }
  const model = currentModel();
  const effort = thinking || "medium";
  const live = addLiveBot();
  let textAccum = "";
  let searchStatus = "";
  let searchSources: SourceLink[] = [];
  const stages: StageName[] = [];
  void refresh();
  try {
    const body: {
      model: string;
      messages: { role: string; content: string }[];
      stream: boolean;
      think: string;
      pi_target: string;
      pi_mesh: string;
    } = {
      model,
      messages: [],
      stream: true,
      think: effort,
      pi_target: "auto",
      pi_mesh: "on",
    };
    const sys = (byId<HTMLTextAreaElement>("sys").value || "").trim();
    if (sys) body.messages.push({ role: "system", content: sys });
    turns.forEach((turn) => {
      if (turn.role === "user" || turn.role === "assistant") {
        body.messages.push({ role: turn.role, content: turn.content });
      }
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
      keepPartial(live, textAccum, text, effort, searchNow(), stages);
      return;
    }
    if (lastErr || !response) throw lastErr || new Error("no response");

    let streamedEffort = response.headers.get("X-Pi-Think") || effort;
    searchStatus = response.headers.get("X-Pi-Search") || "";
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    if (!contentType.includes("event-stream")) {
      const textBody = await response.text();
      let payload: {
        error?: string;
        pi_think?: string;
        pi_search?: string;
        pi_sources?: SourceLink[];
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
      turns.push({
        role: "assistant",
        content: answer,
        effort: payload.pi_think || streamedEffort,
        search,
        stages: doneStages,
      });
      paint();
      if (spoken) speakText(answer);
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
          pi_search?: string;
          pi_sources?: SourceLink[];
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
        }
        if (payload.pi_think) streamedEffort = payload.pi_think;
      }
    }
    if (stopAsked) {
      keepPartial(live, textAccum, text, streamedEffort || effort, searchNow(), stages);
      return;
    }
    if (streamErr || (!response.ok && !textAccum)) {
      const msg = shownError(streamErr || "HTTP " + response.status);
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
    const search = searchNow();
    turns.push({
      role: "assistant",
      content: textAccum,
      effort: streamedEffort,
      search,
      stages,
    });
    paint();
    if (spoken) speakText(textAccum);
  } catch (err) {
    if (stopAsked) {
      keepPartial(live, textAccum, text, effort, searchStatus ? { status: searchStatus, sources: searchSources } : null, stages);
      return;
    }
    const msg = shownError(err);
    live.setText(msg);
    live.markErr();
    live.finish(msg, true, "", "", null, stages);
    const retry = el("button", "retry", "Retry");
    retry.onclick = () => {
      live.root.remove();
      void sendText(text, true);
    };
    live.root.appendChild(retry);
  } finally {
    sending = false;
    stopAsked = false;
    turnCtrl = null;
    syncSend();
    byId<HTMLTextAreaElement>("q").focus();
  }
}

async function send(): Promise<void> {
  voiceNote("");
  const box = byId<HTMLTextAreaElement>("q");
  let text = box.value.trim();
  const attached = box.dataset.attachText || "";
  if (attached) {
    text = text ? text + "\n\n---\n" + attached : attached;
    clearAttach();
  }
  if (!text) {
    speakThisReply = false;
    return;
  }
  const spoken = speakThisReply;
  speakThisReply = false;
  box.value = "";
  autoGrow(box);
  await sendText(text, false, spoken);
}

function syncSend(): void {
  const box = byId<HTMLTextAreaElement>("q");
  const go = byId<HTMLButtonElement>("go");
  const has = (box.value || "").trim() || box.dataset.attachText;
  if (sending) {
    go.disabled = false;
    go.classList.add("stop");
    go.setAttribute("aria-label", "Stop");
    return;
  }
  go.classList.remove("stop");
  go.setAttribute("aria-label", "Send");
  go.toggleAttribute("disabled", !has);
}

function autoGrow(box: HTMLTextAreaElement): void {
  box.style.height = "auto";
  box.style.height = Math.min(180, box.scrollHeight) + "px";
}

function threadAsMd(): string {
  let out = "# Pi GPT 1.0\n\n";
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
  byId("fileTag").classList.remove("on");
  byId<HTMLInputElement>("attach").value = "";
  syncSend();
}

function loadFile(file: File | null): void {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const text = String(reader.result || "");
    byId<HTMLTextAreaElement>("q").dataset.attachText = text;
    byId("fileName").textContent = file.name + " (" + Math.round(text.length / 1024 * 10) / 10 + " KB)";
    byId("fileTag").classList.add("on");
    syncSend();
  };
  reader.readAsText(file);
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
  const button = byId("btnVoice");
  button.classList.toggle("on", listening);
  button.classList.toggle("live", listening);
  button.setAttribute("aria-pressed", listening ? "true" : "false");
  button.setAttribute("aria-label", listening ? "Stop listening" : "Voice");
}

function toggleVoice(): void {
  if (listening) {
    listenHandle?.stop();
    listening = false;
    listenHandle = null;
    paintVoice();
    return;
  }
  stopSpeaking();
  voiceNote("");
  if (!speechReady()) {
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    return;
  }
  const box = byId<HTMLTextAreaElement>("q");
  const handle = startListening({
    onInterim(text) {
      box.value = text;
      autoGrow(box);
      syncSend();
    },
    onFinal(text) {
      box.value = text;
      autoGrow(box);
      speakThisReply = true;
      voiceNote("");
      void send();
    },
    onError() {
      voiceNote("Voice did not catch that. Try again.");
    },
    onEnd() {
      listening = false;
      listenHandle = null;
      paintVoice();
    },
  });
  if (!handle) {
    voiceNote("Voice needs Chrome's built-in speech recognition.");
    return;
  }
  listening = true;
  listenHandle = handle;
  paintVoice();
}

byId("go").onclick = () => {
  if (sending) {
    stopAsked = true;
    stopSpeaking();
    turnCtrl?.abort();
    return;
  }
  void send();
};
const composer = byId<HTMLTextAreaElement>("q");
composer.addEventListener("input", () => {
  autoGrow(composer);
  syncSend();
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
  event.preventDefault();
  void send();
});
document.querySelectorAll(".think-btn").forEach((btn) => {
  (btn as HTMLButtonElement).onclick = () => {
    thinking = btn.getAttribute("data-think") || "medium";
    document.querySelectorAll(".think-btn").forEach((other) => {
      other.classList.toggle("on", other === btn);
    });
  };
});
byId("btnVoice").onclick = () => toggleVoice();
byId("btnIo").onclick = () => setSettingsOpen(true);
byId("btnCloseIo").onclick = () => setSettingsOpen(false);
byId("overlay").onclick = () => setSettingsOpen(false);
byId("btnClear").onclick = () => {
  turns.length = 0;
  stopSpeaking();
  paint();
  setSettingsOpen(false);
};
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
  if (!document.hidden) void refresh();
});
