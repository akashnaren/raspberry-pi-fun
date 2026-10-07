import { userMessagePieces, type DocCard } from "../attach/message";
import { byId, el } from "../core/dom";
import { browserStorage } from "../core/settings";
import { TURNS_KEY, ui } from "../core/state";
import type { SearchInfo, StageName, Turn } from "../core/types";
import { turns } from "../main";
import { cardsFrom } from "../render/images";
import { hideEmpty, setBodyContent, showSearch, thoughtPanel, thumbIcon } from "../ui/panels";
import { sendText } from "./send";
import { stageText, visibleReply } from "./text";
import { abortTurnImages, scheduleImages } from "./turn-images";

export function attachLabel(parent: HTMLElement, prompt: string, answer: string): void {
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

export function copyButton(text: string): HTMLButtonElement {
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

export function promptBefore(index: number): string {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (turns[i].role === "user") return turns[i].content;
  }
  return "";
}

export function trail(stages: StageName[] | undefined, search: SearchInfo | null): HTMLElement | null {
  const names = (stages || []).filter((name) => name === "thinking" || name === "searching");
  if (!names.length) return null;
  const row = el("div", "trail");
  names.forEach((name, index) => {
    if (index) row.appendChild(el("span", "trail-dot", "·"));
    row.appendChild(el("span", "", stageText(name, search)));
  });
  return row;
}

export function persistTurns(): void {
  const store = browserStorage("session");
  if (!store) return;
  if (!turns.length) {
    store.removeItem(TURNS_KEY);
    return;
  }
  const slim = turns.slice(-40).map((item) => ({
    role: item.role,
    content: item.content,
    hidden: item.hidden || "",
    effort: item.effort || "",
    search: item.search || null,
    stages: item.stages || [],
    mode: item.mode || "",
    route: item.route || "",
    thought: item.thought || "",
    thoughtSeconds: item.thoughtSeconds || 0,
    images: item.images || null,
    stopped: Boolean(item.stopped),
    attachment: item.attachment || null,
  }));
  try {
    store.setItem(TURNS_KEY, JSON.stringify(slim));
  } catch {
    /* the tab can refuse a large transcript */
  }
}

export function restoreTurns(): void {
  const store = browserStorage("session");
  const raw = store?.getItem(TURNS_KEY) || "";
  if (!raw || turns.length) return;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return;
    for (const row of parsed.slice(-40)) {
      if (!row || typeof row !== "object") continue;
      const item = row as Record<string, unknown>;
      if (item.role !== "user" && item.role !== "assistant") continue;
      if (typeof item.content !== "string") continue;
      const turn: Turn = {
        role: item.role,
        content: item.content,
        hidden: typeof item.hidden === "string" ? item.hidden : "",
        effort: typeof item.effort === "string" ? item.effort : "",
        search: item.search && typeof item.search === "object" ? (item.search as SearchInfo) : null,
        stages: Array.isArray(item.stages) ? (item.stages as StageName[]) : [],
        mode: typeof item.mode === "string" ? item.mode : "",
        route: typeof item.route === "string" ? item.route : "",
        thought: typeof item.thought === "string" ? item.thought : "",
        thoughtSeconds: typeof item.thoughtSeconds === "number" ? item.thoughtSeconds : 0,
        stopped: item.stopped === true,
      };
      if (item.role === "assistant") turn.images = cardsFrom(item.images) ?? [];
      if (item.attachment && typeof item.attachment === "object") {
        const card = item.attachment as DocCard;
        if (typeof card.name === "string" && typeof card.route === "string") turn.attachment = card;
      }
      turns.push(turn);
    }
  } catch {
    /* ignore a broken transcript */
  }
}

export function paint(): void {
  const log = byId("log");
  log.replaceChildren();
  if (!turns.length) {
    const empty = el("div", "empty");
    empty.id = "empty";
    empty.appendChild(el("p", "", "Ask anything."));
    log.appendChild(empty);
    persistTurns();
    return;
  }
  turns.forEach((item, index) => {
    if (item.role === "user") addUser(item.content, index);
    else addFinishedBot(item, index);
  });
  log.lastElementChild?.scrollIntoView({ block: "end" });
  persistTurns();
}

export function docCardNode(card: DocCard): HTMLElement {
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

export function addUser(text: string, index: number): HTMLElement {
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

export function beginEdit(index: number): void {
  if (ui.sending) return;
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
    for (const gone of turns.slice(index)) abortTurnImages(gone);
    turns.splice(index);
    void sendText(next, false, false, { hidden, attachment });
  };
  actions.appendChild(cancel);
  actions.appendChild(save);
  row.appendChild(actions);
  box.focus();
}

export function addFinishedBot(item: Turn, index: number): HTMLElement {
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
  scheduleImages(row, item);
  return row;
}

export function retryIcon(): SVGSVGElement {
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

export function retryButton(onClick: () => void): HTMLButtonElement {
  const again = el("button", "icon-btn retry") as HTMLButtonElement;
  again.type = "button";
  again.setAttribute("aria-label", "Retry");
  again.title = "Retry";
  again.appendChild(retryIcon());
  again.onclick = onClick;
  return again;
}

export function regenerate(): void {
  if (ui.sending) return;
  if (!turns.length || turns[turns.length - 1].role !== "assistant") return;
  abortTurnImages(turns[turns.length - 1]);
  turns.pop();
  const last = turns[turns.length - 1];
  if (!last || last.role !== "user") return;
  paint();
  void sendText(last.content, true);
}

export function lastAssistantText(): string {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn.role === "assistant" && !turn.stopped) return turn.content;
  }
  return "";
}

export function userBeforeCurrent(): string {
  let seen = 0;
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (turns[index].role !== "user") continue;
    seen += 1;
    if (seen === 2) return turns[index].content;
  }
  return "";
}
