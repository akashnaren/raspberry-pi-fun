import { byId, el } from "../core/dom";
import type { LiveTurn, SearchInfo, StageName } from "../core/types";
import { turns } from "../main";
import { hideEmpty, motionReduced, setBodyContent, showSearch, thoughtPanel } from "../ui/panels";
import { stageText, thoughtSummary, visibleReply, waitingCopy, withoutThinkTags } from "./text";
import { attachLabel, paint, trail } from "./transcript";

export function addLiveBot(expectPro = false): LiveTurn {
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
        setBodyContent(body, text, !failed, false, search?.sources?.length || 0);
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

export function keepPartial(
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
      stopped: true,
    });
    paint();
    return;
  }
  live?.root.remove();
}
