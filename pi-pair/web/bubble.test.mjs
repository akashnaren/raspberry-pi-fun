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
window.MESH_DEFAULT_MODEL = "qwen3:0.6b";
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
const chatBodies = [];
const pendingImages = [];
let imageCalls = 0;
let imageCards = [];

class FakeImage {
  constructor() {
    this.onload = null;
    this.onerror = null;
    this.referrerPolicy = "";
    this._src = "";
  }

  set src(value) {
    this._src = value;
    pendingImages.push(this);
  }

  get src() {
    return this._src;
  }
}

globalThis.Image = FakeImage;
window.Image = FakeImage;

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

globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.includes("/health")) {
    return new Response(JSON.stringify({ peers: [{ models: ["qwen3:0.6b"] }] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (url.includes("/v1/chat/completions")) {
    if (init && typeof init.body === "string") chatBodies.push(init.body);
    const stream = openStream();
    streams.push(stream);
    return new Response(stream.readable, {
      status: 200,
      headers: { "content-type": "text/event-stream" },
    });
  }
  if (url.includes("/v1/images")) {
    imageCalls += 1;
    return new Response(JSON.stringify({ pi_images: imageCards }), {
      status: 200,
      headers: { "content-type": "application/json" },
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

first.push({
  choices: [{ index: 0, delta: { content: " is by the east window and the morning light stays warm there." } }],
});
await waitFor("rest of the token", () => {
  const bubble = streamingBubble();
  return Boolean(bubble && bubble.textContent.includes("east window"));
});
if (imageCalls !== 0) {
  throw new Error("image request started before the answer finished");
}
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
if (imageCalls !== 1) {
  throw new Error("finished answer should ask for images once, got " + imageCalls);
}
if (document.querySelector(".image-cards")) {
  throw new Error("empty image reply mounted a strip");
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
if (imageCalls !== 1) {
  throw new Error("a turn with no visible text requested images");
}

const photo =
  "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg/320px-Tour_Eiffel_Wikimedia_Commons.jpg";
imageCards = [
  {
    url: photo,
    alt: "Eiffel Tower. Lattice tower in Paris",
    title: "Eiffel Tower",
    caption: "Lattice tower in Paris",
    source: "https://en.wikipedia.org/wiki/Eiffel_Tower",
    width: 320,
    height: 480,
  },
];
const pictured = await sendTurn("What does the Eiffel Tower look like?");
await waitFor("picture live row", () => document.querySelector(".msg.bot.streaming"));
if (imageCalls !== 1) {
  throw new Error("image request started before the pictured answer finished");
}
pictured.push({ choices: [{ index: 0, delta: { content: "The tower stands in Paris." } }] });
pictured.end();
await waitFor("pictured answer", () => {
  return !document.querySelector(".msg.streaming")
    && assistantBubbles().some((node) => node.textContent.includes("stands in Paris"));
});
await waitFor("image preload", () => pendingImages.length >= 1);
if (document.querySelector(".image-cards")) {
  throw new Error("strip mounted before the image loaded");
}
pendingImages[0].onload();
await waitFor("image strip", () => document.querySelector(".image-cards"));
const picturedRow = [...document.querySelectorAll(".msg.bot")].find((node) =>
  node.textContent.includes("stands in Paris"),
);
const picturedBody = picturedRow && picturedRow.querySelector(".body");
const strip = picturedRow && picturedRow.querySelector(".image-cards");
if (!picturedBody || !strip || picturedBody.nextElementSibling !== strip) {
  throw new Error("strip should sit after the answer text");
}
if (picturedBody.querySelector(".image-cards")) {
  throw new Error("cards were written into the answer text");
}

const pictures = document.getElementById("pictures");
if (!pictures || !pictures.checked) throw new Error("pictures should default on");
pictures.checked = false;
pictures.dispatchEvent(new window.Event("change"));
const callsBeforeToggle = imageCalls;
const quiet = await sendTurn("Explain recursion briefly");
await waitFor("quiet live row", () => document.querySelector(".msg.bot.streaming"));
quiet.push({ choices: [{ index: 0, delta: { content: "A function can call itself." } }] });
quiet.end();
await waitFor("quiet answer", () => {
  return !document.querySelector(".msg.streaming")
    && assistantBubbles().some((node) => node.textContent.includes("call itself"));
});
if (imageCalls !== callsBeforeToggle) {
  throw new Error("pictures off still requested images");
}
const lastChat = chatBodies[chatBodies.length - 1] || "";
if (lastChat.includes("upload.wikimedia.org") || lastChat.includes("pi_images")) {
  throw new Error("image cards were sent back to chat");
}

await new Promise((resolve) => setTimeout(resolve, 800));
if (document.querySelector(".msg.streaming")) {
  const stray = streams[streams.length - 1];
  stray.push({ choices: [{ index: 0, delta: { content: "Nothing else to add." } }] });
  stray.end();
  await waitFor("stray retry settled", () => !document.querySelector(".msg.streaming"));
}

const greet = "I'm here to help with anything you need. How can I assist you today?";
const seeded = await sendTurn("hello there");
seeded.push({ choices: [{ index: 0, delta: { content: greet } }] });
seeded.end();
await waitFor("greeting landed", () => assistantBubbles().some((node) => node.textContent.includes("assist you today")));
const greetingBubbles = () => assistantBubbles().filter((node) => node.textContent.includes("assist you today")).length;
const bubblesBefore = greetingBubbles();
const streamsBefore = streams.length;
const bodiesBefore = chatBodies.length;
window.speechSynthesis = {
  speaking: false,
  pending: false,
  getVoices() { return []; },
  cancel() {},
  resume() {},
  speak() { throw new Error("a repeated reply was spoken"); },
};
const copied = await sendTurn("top ev to buy");
copied.push({ choices: [{ index: 0, delta: { content: greet } }] });
copied.end();
await waitFor("fresh retry", () => chatBodies.slice(bodiesBefore).some((raw) => {
  const parsed = JSON.parse(raw);
  return parsed.messages.length === 1 && parsed.messages[0].content === "top ev to buy";
}));
if (greetingBubbles() !== bubblesBefore) throw new Error("the repeated reply was pushed");
const fresh = JSON.parse(chatBodies.find((raw) => {
  const parsed = JSON.parse(raw);
  return parsed.messages.length === 1 && parsed.messages[0].content === "top ev to buy";
}));
if (fresh.messages.some((row) => row.role === "assistant")) {
  throw new Error("fresh retry kept earlier replies: " + JSON.stringify(fresh.messages));
}
const freshCount = chatBodies.filter((raw) => {
  const parsed = JSON.parse(raw);
  return parsed.messages.length === 1 && parsed.messages[0].content === "top ev to buy";
}).length;
if (freshCount !== 1) throw new Error("expected one fresh retry, saw " + freshCount);
const accepted = streams[streams.length - 1];
accepted.push({ choices: [{ index: 0, delta: { content: greet } }] });
accepted.end();
await waitFor("second repeat kept", () => {
  return greetingBubbles() === bubblesBefore + 1 && !document.querySelector(".msg.streaming");
});
if (streams.length !== streamsBefore + 2) throw new Error("the cap retried more than once");

for (const id of timers) rawClearInterval(id);
console.log("ok");
