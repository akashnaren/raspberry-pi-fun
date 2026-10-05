import fs from "fs";
import { register } from "node:module";
import { parseHTML } from "linkedom";
import { primaryKind, primaryLabel } from "./src/primary-action.ts";
import { shouldPlaySplash } from "./src/splash.ts";

if (primaryKind(false, false) !== "voice" || primaryLabel("voice") !== "Voice mode") {
  throw new Error("empty composer is not the voice waveform");
}
if (primaryKind(false, true) !== "send" || primaryLabel("send") !== "Send") {
  throw new Error("typed composer is not send");
}
if (primaryKind(true, true) !== "stop") throw new Error("in-flight composer is not stop");
if (!shouldPlaySplash(null, "navigate")) throw new Error("first load skipped the splash");
if (shouldPlaySplash("1", "navigate")) throw new Error("session replayed the splash");
if (!shouldPlaySplash("1", "reload")) throw new Error("reload could not replay the splash");

await register("./ts-resolve.mjs", import.meta.url);

const html = fs.readFileSync(new URL("./index.html", import.meta.url), "utf8");
const { document, window } = parseHTML(html);
const memory = new Map();
function memStore() {
  return {
    getItem(key) { return memory.has(key) ? memory.get(key) : null; },
    setItem(key, value) { memory.set(key, String(value)); },
    removeItem(key) { memory.delete(key); },
  };
}
window.sessionStorage = memStore();
window.localStorage = memStore();
window.confirm = () => true;
window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
window.matchMedia = () => ({
  matches: true,
  media: "(prefers-reduced-motion: reduce)",
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return false; },
});
if (typeof window.KeyboardEvent !== "function") {
  window.KeyboardEvent = class KeyboardEvent extends window.Event {
    constructor(type, init = {}) {
      super(type, init);
      this.key = init.key || "";
      this.shiftKey = Boolean(init.shiftKey);
      this.ctrlKey = Boolean(init.ctrlKey);
      this.metaKey = Boolean(init.metaKey);
    }
  };
}
window.setTimeout = globalThis.setTimeout.bind(globalThis);
window.clearTimeout = globalThis.clearTimeout.bind(globalThis);
window.setInterval = () => 0;
window.clearInterval = () => {};
window.requestAnimationFrame = (fn) => window.setTimeout(() => fn(0), 16);
performance.getEntriesByType = () => [{ type: "navigate" }];
globalThis.window = window;
globalThis.document = document;
globalThis.localStorage = window.localStorage;
globalThis.sessionStorage = window.sessionStorage;
globalThis.confirm = window.confirm;
Object.defineProperty(document, "compatMode", { value: "CSS1Compat" });
window.MESH_DEFAULT_MODEL = "qwen2.5:0.5b";

const streams = [];
function openStream() {
  const encoder = new TextEncoder();
  let pending = null;
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
    },
  });
  return {
    readable,
    push(value) {
      const bytes = encoder.encode("data: " + JSON.stringify(value) + "\n\n");
      if (pending) {
        const controller = pending;
        pending = null;
        controller.enqueue(bytes);
        return;
      }
      queued.push(bytes);
    },
    end() {
      const bytes = encoder.encode("data: [DONE]\n\n");
      if (pending) pending.enqueue(bytes);
      else queued.push(bytes);
    },
  };
}

globalThis.fetch = async (input) => {
  const url = typeof input === "string" ? input : input.url;
  if (String(url).includes("/health")) {
    return new Response(JSON.stringify({ peers: [{ models: ["qwen2.5:0.5b"] }] }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (String(url).includes("/v1/chat/completions")) {
    const stream = openStream();
    streams.push(stream);
    return new Response(stream.readable, {
      status: 200,
      headers: { "content-type": "text/event-stream", "X-Pi-Mode": "auto", "X-Pi-Route": "flash" },
    });
  }
  return new Response("missing", { status: 404 });
};

await import("./src/main.ts");

const go = document.getElementById("go");
if (!go.classList.contains("voice") || go.getAttribute("aria-label") !== "Voice mode") {
  throw new Error("empty primary was " + go.className + " " + go.getAttribute("aria-label"));
}
if (!go.querySelector(".voice-mark") || go.querySelectorAll(".voice-mark, .send-mark").length < 2) {
  throw new Error("primary marks missing");
}
const box = document.getElementById("q");
box.value = "hello mesh";
box.dispatchEvent(new window.Event("input"));
if (!go.classList.contains("send") || go.getAttribute("aria-label") !== "Send") {
  throw new Error("typed primary stayed " + go.className);
}
box.value = "";
box.dispatchEvent(new window.Event("input"));
if (!go.classList.contains("voice") || go.classList.contains("send")) {
  throw new Error("clearing the draft did not restore the waveform");
}

const brand = document.getElementById("brand");
if (!brand.classList.contains("brand-logo")) throw new Error("idle header is not the logo");
if (brand.classList.contains("brand-title")) throw new Error("idle header showed the title");
if (!document.querySelector("#brandMark svg")) throw new Error("mark was not drawn");
if (!document.getElementById("brandName").textContent.includes("OpenPi")) {
  throw new Error("title text is missing");
}
box.value = "plot a curve";
box.dispatchEvent(new window.Event("input"));
if (!brand.classList.contains("brand-title") || brand.classList.contains("brand-logo")) {
  throw new Error("typing did not morph the logo into the title");
}
box.value = "";
box.dispatchEvent(new window.Event("input"));
if (!brand.classList.contains("brand-logo") || brand.classList.contains("brand-title")) {
  throw new Error("clearing the draft did not return to the logo");
}
if (!document.getElementById("btnIo").querySelector("svg path[d*='10.2 2.8']")) {
  throw new Error("settings control is not a gear");
}
if (!document.getElementById("serviceNow") || !document.getElementById("serviceLog")) {
  throw new Error("settings has no status log");
}

document.getElementById("btnIo").click();
if (!document.getElementById("ioPanel").classList.contains("open")) throw new Error("settings stayed closed");
document.querySelector('[data-theme-choice="light"]').click();
if (document.documentElement.getAttribute("data-theme") !== "light") throw new Error("light theme did not apply");
if (!window.localStorage.getItem("openpi.settings").includes('"theme":"light"')) {
  throw new Error("theme was not stored");
}
document.querySelector("#ioPanel [data-mode='pro']").click();
if (document.getElementById("modeLabel").textContent !== "Pro") throw new Error("settings did not select Pro");
document.querySelector("#ioPanel [data-think='low']").click();
if (!document.querySelector('.think-btn[data-think="low"]').classList.contains("on")) {
  throw new Error("thinking did not follow settings");
}
const silence = document.getElementById("voiceSilence");
silence.value = "900";
silence.dispatchEvent(new window.Event("change"));
if (silence.value !== "900") throw new Error("silence did not stick");
const enter = document.getElementById("enterSend");
enter.checked = false;
enter.dispatchEvent(new window.Event("change"));
const before = streams.length;
box.value = "should not send";
box.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
if (streams.length !== before) throw new Error("Enter sent while the setting was off");
enter.checked = true;
enter.dispatchEvent(new window.Event("change"));

if (document.querySelector("[data-think] .info-dot")) {
  throw new Error("think controls still have info icons");
}
if (document.querySelectorAll("[data-mode] .info-dot").length < 6) {
  throw new Error("model info icons missing");
}
document.getElementById("modeBtn").click();
const menu = document.getElementById("modePop");
if (menu.hidden) throw new Error("Auto menu did not open");
const beforeMode = document.getElementById("modeLabel").textContent;
const flashInfo = menu.querySelector('[data-mode="flash"] .info-dot');
flashInfo.dispatchEvent(new window.Event("click", { bubbles: true }));
const tip = document.getElementById("modelTip");
if (!tip || tip.hidden || !tip.textContent.includes("0.5b") || !tip.textContent.includes("1.5b")) {
  throw new Error("model tip did not explain Flash and Pro");
}
if (document.getElementById("modeLabel").textContent !== beforeMode) {
  throw new Error("model info changed the mode");
}
if (menu.hidden) throw new Error("model info closed the menu");
menu.querySelector('[data-mode="auto"]').click();
if (document.getElementById("modeLabel").textContent !== "Auto" || !menu.hidden) {
  throw new Error("menu did not return to Auto");
}
if (!tip.hidden) throw new Error("selecting a mode left the tip open");

box.value = "Where is the bench?";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 30));
const live = streams[streams.length - 1];
const sources = [0, 1, 2, 3, 4].map((i) => ({
  title: "Source " + i,
  url: "https://ex" + i + ".test/item",
}));
live.push({ pi_status: "thinking", pi_mode: "auto", pi_route: "flash" });
live.push({ pi_status: "searching", pi_search: "ok", pi_sources: sources });
live.push({ pi_status: "answering" });
live.push({ choices: [{ delta: { content: "By the east window." } }] });
live.end();
await new Promise((resolve) => setTimeout(resolve, 80));
const pill = document.querySelector(".sources-pill");
if (!pill || !pill.textContent.includes("5 sources")) {
  throw new Error("sources pill missing: " + document.getElementById("log").innerHTML);
}
if (pill.querySelectorAll(".sources-fav").length !== 3) throw new Error("favicon stack size");
pill.click();
const panel = document.getElementById("sourcesPanel");
if (!panel.classList.contains("open")) throw new Error("sources panel stayed closed");
const anchors = panel.querySelectorAll(".sources-links a");
if (anchors.length !== 5) throw new Error("panel links " + anchors.length);
if (!panel.textContent.includes("Thinking") || !panel.textContent.includes("Searched web")) {
  throw new Error("panel skipped the steps");
}
if (!document.querySelector(".mode-chip") || !document.querySelector(".mode-chip").textContent.includes("Auto · Flash")) {
  throw new Error("mode chip missing");
}
document.getElementById("sourcesClose").click();
if (panel.classList.contains("open")) throw new Error("sources panel did not close");

window.confirm = () => false;
const bubbles = document.querySelectorAll(".msg").length;
document.getElementById("btnClear").click();
if (document.querySelectorAll(".msg").length !== bubbles) throw new Error("clear ignored cancel");
window.confirm = () => true;
document.getElementById("btnClear").click();
if (document.querySelector(".msg")) throw new Error("confirmed clear left messages");

console.log("ok");
