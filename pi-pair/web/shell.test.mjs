import fs from "fs";
import { register } from "node:module";
import { parseHTML } from "linkedom";
import { modeChipText, scrubAssistant } from "./src/copy.ts";
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
if (modeChipText("flash", "canned") !== "Flash" || modeChipText("auto", "canned") !== "Auto") {
  throw new Error("a map route leaked into the mode chip");
}
if (modeChipText("auto", "pro") !== "Auto · Pro") throw new Error("Pro route lost its label");
const leaked = scrubAssistant("The Civic is common. I used medium effort in Flash mode.");
if (/effort|flash mode|can't assist/i.test(leaked) || !leaked.includes("Civic")) {
  throw new Error("reply still named the thinking control: " + leaked);
}
const fenced = scrubAssistant('Keep this.\n```chart\n{"title":"Flash mode"}\n```');
if (!fenced.includes("```chart") || !fenced.includes("Flash mode")) {
  throw new Error("a chart fence was scrubbed: " + fenced);
}

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
window.MESH_DEFAULT_MODEL = "qwen3:0.6b";

const streams = [];
const sentBodies = [];
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

globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (String(url).includes("/health")) {
    return new Response(JSON.stringify({
      peers: [{ models: ["qwen3:0.6b"] }],
      modes: { flash: "qwen3:0.6b", pro: "qwen3:1.7b" },
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (String(url).includes("/v1/chat/completions")) {
    if (init && init.body) sentBodies.push(String(init.body));
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
box.value = "";
box.dispatchEvent(new window.KeyboardEvent("keydown", { key: "p", bubbles: true }));
if (!brand.classList.contains("brand-title") || brand.classList.contains("brand-logo")) {
  throw new Error("the first key did not morph the logo into the title");
}
box.dispatchEvent(new window.KeyboardEvent("keyup", { key: "p", bubbles: true }));
if (brand.classList.contains("brand-title")) {
  throw new Error("a key that did not fill the composer left the title up");
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
if (!document.getElementById("btnIo").querySelector("svg path[d*='M12 15.5']")) {
  throw new Error("settings control is not a gear");
}
for (const id of ["serviceNow", "serviceLog", "voiceSilence", "modelSel", "btnMd", "btnTxt", "btnClear"]) {
  if (document.getElementById(id)) throw new Error(id + " is still in settings");
}
if (document.querySelector("#ioPanel [data-think]")) {
  throw new Error("settings still has a thinking control");
}
if (document.querySelector(".think-btn .info-dot")) {
  throw new Error("thinking controls still show an info icon");
}
if (document.querySelectorAll(".mode-opt .info-dot").length !== 3) {
  throw new Error("model choices lost their info icons");
}
if (!document.querySelector('.think-btn[data-think="low"]')) {
  throw new Error("the composer lost Low");
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
document.querySelector('.think-btn[data-think="low"]').click();
if (!document.querySelector('.think-btn[data-think="low"]').classList.contains("on")) {
  throw new Error("composer thinking did not change");
}
const enter = document.getElementById("enterSend");
enter.checked = false;
enter.dispatchEvent(new window.Event("change"));
const before = streams.length;
box.value = "should not send";
box.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
if (streams.length !== before) throw new Error("Enter sent while the setting was off");
enter.checked = true;
enter.dispatchEvent(new window.Event("change"));

document.getElementById("modeBtn").click();
const menu = document.getElementById("modePop");
if (menu.hidden) throw new Error("Auto menu did not open");
const autoInfo = menu.querySelector('[aria-label="About Auto"]');
const modeBefore = document.getElementById("modeLabel").textContent;
autoInfo.click();
if (document.getElementById("modeLabel").textContent !== modeBefore) {
  throw new Error("info tap changed the model");
}
const autoTip = document.getElementById("tip-menu-auto");
if (autoTip.hidden || autoInfo.getAttribute("aria-expanded") !== "true") {
  throw new Error("info tap did not open the tip");
}
const flashInfo = menu.querySelector('[aria-label="About Flash"]');
flashInfo.click();
if (!autoTip.hidden || document.getElementById("tip-menu-flash").hidden) {
  throw new Error("two info tips stayed open");
}
document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
if (!document.getElementById("tip-menu-flash").hidden) throw new Error("Escape left the tip open");
flashInfo.dispatchEvent(new window.Event("pointerenter", { bubbles: true }));
if (document.getElementById("tip-menu-flash").hidden) throw new Error("fine pointer did not show the tip");
document.dispatchEvent(new window.Event("click", { bubbles: true }));
if (!document.getElementById("tip-menu-flash").hidden) throw new Error("outside click left the tip open");
menu.querySelector('[data-mode="auto"]').click();
if (document.getElementById("modeLabel").textContent !== "Auto" || !menu.hidden) {
  throw new Error("menu did not return to Auto");
}
await new Promise((resolve) => setTimeout(resolve, 30));
const flashText = document.getElementById("tip-menu-flash").textContent;
const proText = document.getElementById("tip-menu-pro").textContent;
if (flashText !== "qwen3:0.6b, the fast resident model.") {
  throw new Error("flash tip was not built from health: " + flashText);
}
if (proText !== "qwen3:1.7b, loaded when the question needs it.") {
  throw new Error("pro tip was not built from health: " + proText);
}
if (document.getElementById("tip-set-flash").textContent !== flashText) {
  throw new Error("settings flash tip did not follow health");
}
if (document.getElementById("tip-set-pro").textContent !== proText) {
  throw new Error("settings pro tip did not follow health");
}
if (
  html.includes("qwen2.5:0.5b, the fast resident model")
  || html.includes("qwen3:0.6b, the fast resident model")
  || html.includes("qwen3:1.7b, loaded when the question needs it")
) {
  throw new Error("source html still hardcodes a model tip");
}
if (autoTip.textContent !== "Routes Flash or Pro from the question.") {
  throw new Error("auto tip changed");
}

box.value = "Where is the bench?";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 30));
const live = streams[streams.length - 1];
const sources = [0, 1, 2, 3, 4].map((i) => ({
  title: "Source " + i,
  url: "https://ex" + i + ".test/item",
}));
live.push({ pi_status: "waiting", pi_mode: "auto", pi_route: "flash" });
await new Promise((resolve) => setTimeout(resolve, 20));
if (!document.body.textContent.includes("Waiting for a free slot")) {
  throw new Error("waiting stage was not quiet text");
}
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
if (document.querySelector("details.thought")) {
  throw new Error("Thought for Ns appeared when the model did not think");
}

box.value = "Explain why the sky looks blue.";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 40));
const thoughtStream = streams[streams.length - 1];
thoughtStream.push({ choices: [{ delta: { reasoning_content: "count the wavelengths" } }] });
thoughtStream.push({ choices: [{ delta: { content: "Blue light scatters more." } }] });
thoughtStream.end();
await new Promise((resolve) => setTimeout(resolve, 80));
const thought = document.querySelectorAll("details.thought");
const panelThought = thought[thought.length - 1];
if (!panelThought || panelThought.open) throw new Error("thought panel was not collapsed");
const thoughtLabel = panelThought.querySelector("summary");
if (!thoughtLabel || !/Thought for \d+s/.test(thoughtLabel.textContent || "")) {
  throw new Error("thought summary was " + (thoughtLabel && thoughtLabel.textContent));
}
const botBodies = document.querySelectorAll(".msg.bot .body");
const answerBody = botBodies[botBodies.length - 1];
if (!answerBody || answerBody.textContent.includes("wavelengths") || !answerBody.textContent.includes("Blue light")) {
  throw new Error("answer leaked the thought: " + (answerBody && answerBody.textContent));
}
const beforeFollow = sentBodies.length;
box.value = "And what about sunset colors?";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 40));
const follow = sentBodies[sentBodies.length - 1] || "";
if (sentBodies.length === beforeFollow) throw new Error("follow-up was not posted");
if (follow.includes("wavelengths") || follow.includes("<think")) {
  throw new Error("saved history included the thought");
}
if (!follow.includes("Blue light scatters more.")) throw new Error("follow-up dropped the answer");

const css = fs.readFileSync(new URL("./src/styles.scss", import.meta.url), "utf8");
const titleRule = css.slice(css.indexOf(".brand.brand-title .brand-name"), css.indexOf(".brand.brand-title .brand-name") + 220);
if (!/opacity:\s*1/.test(titleRule)) throw new Error("the OpenPi title still waits on an animation");
if (!/html,\s*body\s*\{[^}]*overflow:\s*hidden/s.test(css)) {
  throw new Error("the page shell can still scroll");
}
if (!/#log\s*\{[^}]*overflow-y:\s*auto/s.test(css) || !css.includes("overscroll-behavior: contain")) {
  throw new Error("the message list is not the scrollport");
}
if (!css.includes(".info-dot") || !/\.info-dot\s*\{[^}]*min-width:\s*32px/s.test(css)) {
  throw new Error("info hit target is under 32px");
}
const page = fs.readFileSync(new URL("./src/main.ts", import.meta.url), "utf8");
if (!page.includes('setAttribute("aria-label", "Retry")') || !page.includes("retryIcon")) {
  throw new Error("retry is still a wrapping word");
}
if (page.includes('"Loading Pro"') || page.includes("'Loading Pro'")) {
  throw new Error("the page still says Loading Pro");
}
if (!html.includes('id="voiceSend"') || !page.includes('voiceCaption("Thinking")')) {
  throw new Error("voice mode has no tap-to-send or thinking caption");
}
if (page.includes('speakText("Thinking")')) throw new Error("thinking is spoken aloud");

const docs = document.getElementById("apiDocsLink");
if (!docs || docs.getAttribute("href") !== "/docs" || docs.getAttribute("target") !== "_blank") {
  throw new Error("API docs link missing from the settings drawer");
}
if (!docs.closest("#ioPanel .drawer-body") || !docs.closest(".set-foot")) {
  throw new Error("API docs link is not at the bottom of the settings drawer");
}
if (!css.includes(".api-docs-link") || !css.includes(".set-foot")) {
  throw new Error("API docs link has no styles");
}
const lightAt = css.indexOf('html[data-theme="light"]');
if (lightAt < 0 || !css.slice(lightAt).includes(".api-docs-link")) {
  throw new Error("API docs link has no light-theme color");
}

console.log("ok");
