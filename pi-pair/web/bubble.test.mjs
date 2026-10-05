import fs from "fs";
import { register } from "node:module";
import { parseHTML } from "linkedom";

await register("./ts-resolve.mjs", import.meta.url);

const html = fs.readFileSync(new URL("./index.html", import.meta.url), "utf8");
const { document, window } = parseHTML(html);
const timers = new Set();
const rawSetInterval = globalThis.setInterval.bind(globalThis);
const rawClearInterval = globalThis.clearInterval.bind(globalThis);

globalThis.window = window;
globalThis.document = document;
Object.defineProperty(document, "compatMode", { value: "CSS1Compat" });
window.MESH_DEFAULT_MODEL = "qwen2.5:0.5b";
window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
window.matchMedia = () => ({
  matches: true,
  media: "(prefers-reduced-motion: reduce)",
  onchange: null,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {
    return false;
  },
});
window.setTimeout = globalThis.setTimeout.bind(globalThis);
window.clearTimeout = globalThis.clearTimeout.bind(globalThis);
window.setInterval = (fn, ms, ...args) => {
  const id = rawSetInterval(fn, ms, ...args);
  timers.add(id);
  return id;
};
window.clearInterval = (id) => {
  timers.delete(id);
  rawClearInterval(id);
};
window.requestAnimationFrame = (fn) => window.setTimeout(() => fn(0), 16);
window.cancelAnimationFrame = window.clearTimeout;

const streams = [];

function openStream() {
  const encoder = new TextEncoder();
  let pending = null;
  let notifyWait = null;
  const queued = [];
  const readable = new ReadableStream({
    pull(controller) {
      if (queued.length) {
        const next = queued.shift();
        if (next == null) controller.close();
        else controller.enqueue(next);
        return;
      }
      pending = controller;
      const notify = notifyWait;
      notifyWait = null;
      notify?.();
    },
  });

  function pushBytes(bytes) {
    if (pending) {
      const controller = pending;
      pending = null;
      controller.enqueue(bytes);
      return;
    }
    queued.push(bytes);
  }

  return {
    readable,
    whenWaiting() {
      if (pending && queued.length === 0) return Promise.resolve();
      return new Promise((resolve) => {
        notifyWait = resolve;
      });
    },
    push(value) {
      const line = typeof value === "string" ? value : JSON.stringify(value);
      pushBytes(encoder.encode(`data: ${line}\n\n`));
    },
    end() {
      pushBytes(encoder.encode("data: [DONE]\n\n"));
    },
  };
}

globalThis.fetch = async (input) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.includes("/health")) {
    return new Response(JSON.stringify({ peers: [{ models: ["qwen2.5:0.5b"] }] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (url.includes("/v1/chat/completions")) {
    const stream = openStream();
    streams.push(stream);
    return new Response(stream.readable, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }
  return new Response("missing", { status: 404 });
};

function assistantBubbles() {
  return [...document.querySelectorAll(".msg.bot > .body")];
}

function streamingBubble() {
  return document.querySelector(".msg.bot.streaming > .body");
}

function assertNoBlankBubble(where) {
  const live = streamingBubble();
  if (live) {
    throw new Error(where + " mounted an assistant bubble: " + JSON.stringify(live.textContent));
  }
  const blank = assistantBubbles().filter((node) => !node.textContent.trim());
  if (blank.length) {
    throw new Error(where + " showed a blank assistant bubble");
  }
}

async function waitFor(label, pred) {
  const start = Date.now();
  while (!pred()) {
    if (Date.now() - start > 2000) {
      throw new Error(label + "\n" + document.getElementById("log").innerHTML);
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

async function sendTurn(text) {
  const before = streams.length;
  document.getElementById("q").value = text;
  document.getElementById("go").onclick();
  await waitFor("chat stream did not start", () => streams.length > before);
  return streams[streams.length - 1];
}

await import("./src/main.ts");

const first = await sendTurn("Where is the bench?");
await waitFor("live row", () => document.querySelector(".msg.bot.streaming"));
await first.whenWaiting();
assertNoBlankBubble("before any token");
const user = document.querySelector(".msg.user > .body");
if (!user || user.textContent !== "Where is the bench?") {
  throw new Error("user bubble was not kept: " + JSON.stringify(user && user.textContent));
}

first.push({ pi_status: "thinking" });
await waitFor("thinking stage", () => document.querySelector('.msg.streaming [data-stage="thinking"]'));
await first.whenWaiting();
assertNoBlankBubble("during thinking");

first.push({ pi_status: "searching" });
await waitFor("searching stage", () => document.querySelector('.msg.streaming [data-stage="searching"]'));
await first.whenWaiting();
assertNoBlankBubble("during searching");

first.push({ pi_status: "answering" });
await waitFor("answering stage", () => document.querySelector('.msg.streaming [data-stage="answering"]'));
await first.whenWaiting();
assertNoBlankBubble("during answering");

first.push({ choices: [{ index: 0, delta: { role: "assistant" } }] });
await first.whenWaiting();
assertNoBlankBubble("after a role delta with no content");

first.push({ choices: [{ index: 0, delta: { content: " \n" } }] });
await first.whenWaiting();
assertNoBlankBubble("after a whitespace token");

first.push({ choices: [{ index: 0, delta: { content: "The bench" } }] });
await waitFor("first token", () => {
  const bubble = streamingBubble();
  return Boolean(bubble && bubble.textContent.includes("The bench") && bubble.dataset.filled === "1");
});
if (assistantBubbles().length !== 1) {
  throw new Error("expected one assistant bubble after the first token");
}

first.push({ choices: [{ index: 0, delta: { content: " is by the east window." } }] });
await waitFor("rest of the token", () => {
  const bubble = streamingBubble();
  return Boolean(bubble && bubble.textContent.includes("east window"));
});
first.end();
await waitFor("finished answer", () => {
  return !document.querySelector(".msg.streaming")
    && document.getElementById("go").getAttribute("aria-label") === "Voice mode"
    && assistantBubbles().some((node) => node.textContent.includes("east window"));
});
assertNoBlankBubble("after the answer");
if (assistantBubbles().length !== 1) {
  throw new Error("finished turn created an extra assistant bubble");
}

const second = await sendTurn("Anything else?");
await waitFor("second live row", () => document.querySelector(".msg.bot.streaming"));
await second.whenWaiting();
assertNoBlankBubble("before tokens on the next turn");
second.push({ pi_status: "thinking" });
await waitFor("second thinking", () => document.querySelector('.msg.streaming [data-stage="thinking"]'));
await second.whenWaiting();
assertNoBlankBubble("during thinking on the next turn");
second.push({ pi_status: "searching" });
await waitFor("second searching", () => document.querySelector('.msg.streaming [data-stage="searching"]'));
await second.whenWaiting();
assertNoBlankBubble("during searching on the next turn");
second.end();
await waitFor("empty turn finished", () => {
  return !document.querySelector(".msg.streaming")
    && document.getElementById("go").getAttribute("aria-label") === "Voice mode";
});
assertNoBlankBubble("after stages with no tokens");
if (assistantBubbles().length !== 1 || !assistantBubbles()[0].textContent.includes("east window")) {
  throw new Error("a turn with only stages left a new or blank assistant bubble");
}

for (const id of timers) rawClearInterval(id);
console.log("ok");
