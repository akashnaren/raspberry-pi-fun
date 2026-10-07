import fs from "fs";
import { register } from "node:module";
import { parseHTML } from "linkedom";
import { scrubAssistant } from "../src/chat/copy.ts";
import { primaryKind, primaryLabel } from "../src/ui/primary-action.ts";
import { shouldPlaySplash } from "../src/ui/splash.ts";

if (primaryKind(false, false) !== "voice" || primaryLabel("voice") !== "Voice mode") {
  throw new Error("empty composer is not the voice waveform");
}
if (primaryKind(false, true) !== "send" || primaryLabel("send") !== "Send") {
  throw new Error("typed composer is not send");
}
if (primaryKind(true, false) !== "stop") throw new Error("in-flight composer is not stop");
if (primaryKind(true, true) !== "send") throw new Error("a draft during a reply is not send");
if (!shouldPlaySplash(null, "navigate")) throw new Error("first load skipped the splash");
if (shouldPlaySplash("1", "navigate")) throw new Error("session replayed the splash");
if (!shouldPlaySplash("1", "reload")) throw new Error("reload could not replay the splash");
const leaked = scrubAssistant("The Civic is common. I used medium effort in Flash mode.");
if (/effort|flash mode|can't assist/i.test(leaked) || !leaked.includes("Civic")) {
  throw new Error("reply still named the thinking control: " + leaked);
}
const fenced = scrubAssistant('Keep this.\n```chart\n{"title":"Flash mode"}\n```');
if (!fenced.includes("```chart") || !fenced.includes("Flash mode")) {
  throw new Error("a chart fence was scrubbed: " + fenced);
}

await register("./support/ts-resolve.mjs", import.meta.url);

const html = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
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
const sentHeaders = [];
function openStream() {
  const encoder = new TextEncoder();
  let pending = null;
  const queued = [];
  const readable = new ReadableStream({
    pull(controller) {
      if (queued.length) {
        const next = queued.shift();
        if (next instanceof Error) controller.error(next);
        else if (next == null) controller.close();
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
    abort() {
      const error = new Error("Aborted");
      error.name = "AbortError";
      if (pending) {
        const controller = pending;
        pending = null;
        controller.error(error);
        return;
      }
      queued.push(error);
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
    if (init && init.headers) sentHeaders.push(init.headers);
    const stream = openStream();
    streams.push(stream);
    const signal = init && init.signal;
    if (signal && typeof signal.addEventListener === "function") {
      if (signal.aborted) stream.abort();
      else signal.addEventListener("abort", () => stream.abort(), { once: true });
    }
    return new Response(stream.readable, {
      status: 200,
      headers: { "content-type": "text/event-stream", "X-Pi-Mode": "auto", "X-Pi-Route": "flash" },
    });
  }
  return new Response("missing", { status: 404 });
};

await import("../src/main.ts");

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
if (flashText !== "Fast answers for everyday questions.") {
  throw new Error("flash tip was not the general sentence: " + flashText);
}
if (proText !== "Slower, more careful answers for harder questions.") {
  throw new Error("pro tip was not the general sentence: " + proText);
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
live.push({ pi_status: "waiting", pi_queue: { position: 2, eta_s: 120 } });
await new Promise((resolve) => setTimeout(resolve, 20));
if (!document.body.textContent.includes("You're #2, about 120 s")) {
  throw new Error("queue line missing: " + document.body.textContent);
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
const actions = document.querySelector(".msg.bot .label-row");
if (!actions) throw new Error("reply action row missing");
if (actions.querySelector(".mode-chip") || actions.querySelector(".effort")) {
  throw new Error("model or effort label is still on the action row");
}
const actionNames = [...actions.querySelectorAll("button")].map((node) => node.getAttribute("aria-label") || "");
for (const needed of ["Thumbs up", "Thumbs down", "Corrected answer", "Copy", "Retry"]) {
  if (!actionNames.includes(needed)) throw new Error("action row lost " + needed);
}
if (/\b(Flash|Pro|Auto|Low|Medium|High)\b/.test(actions.textContent || "")) {
  throw new Error("action row still names the model or effort: " + actions.textContent);
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

const css = fs.readFileSync(new URL("../src/styles/main.scss", import.meta.url), "utf8");
const titleRule = css.slice(css.indexOf(".brand.brand-title .brand-name"), css.indexOf(".brand.brand-title .brand-name") + 220);
if (!/opacity:\s*1/.test(titleRule)) throw new Error("the OpenPi title still waits on an animation");
if (!/html,\s*body\s*\{[^}]*overflow:\s*hidden/s.test(css)) {
  throw new Error("the page shell can still scroll");
}
if (!/#log\s*\{[^}]*overflow-y:\s*auto/s.test(css) || !css.includes("overscroll-behavior: contain")) {
  throw new Error("the message list is not the scrollport");
}
const logRule = css.slice(css.indexOf("#log {"), css.indexOf("#log {") + 900);
if (!/scrollbar-width:\s*none/.test(logRule) || !/-ms-overflow-style:\s*none/.test(logRule)) {
  throw new Error("the message scroller still shows a scrollbar");
}
if (!/&::-webkit-scrollbar\s*\{[^}]*display:\s*none/s.test(logRule)) {
  throw new Error("the webkit message scrollbar is still visible");
}
if (/html,\s*body\s*\{[^}]*scrollbar-width:\s*none/s.test(css)) {
  throw new Error("the page scrollbar was hidden");
}
if (!css.includes(".info-dot") || !/\.info-dot\s*\{[^}]*min-width:\s*32px/s.test(css)) {
  throw new Error("info hit target is under 32px");
}
const page = fs.readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
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
const fileRule = css.slice(css.indexOf(".file-tag {"), css.indexOf(".file-tag {") + 500);
if (!fileRule.includes("&.err")) throw new Error("a failed file chip has no error style");
const newer = document.getElementById("btnNew");
if (!newer || newer.getAttribute("aria-label") !== "New chat") {
  throw new Error("new chat control is missing");
}
if (!page.includes("X-Pi-Request-Id") || !page.includes("couldn't read it")) {
  throw new Error("the page lost the request id or the file chip");
}
if (page.includes("the fast resident model")) {
  throw new Error("tips still name the resident model");
}

document.getElementById("btnIo").click();
document.getElementById("modeBtn").click();
if (document.getElementById("modePop").hidden) throw new Error("menu did not open for dismiss");
document.getElementById("modeBtn").dispatchEvent(new window.Event("pointerdown", { bubbles: true }));
if (document.getElementById("modePop").hidden) {
  throw new Error("pointerdown on the mode button closed the menu");
}
document.body.dispatchEvent(new window.Event("pointerdown", { bubbles: true }));
if (!document.getElementById("modePop").hidden) {
  throw new Error("outside pointerdown left the menu open");
}
document.getElementById("modeBtn").click();
document.getElementById("btnIo").click();
const sourcesPill = document.querySelector(".sources-pill");
if (!sourcesPill) throw new Error("sources pill missing before Escape");
sourcesPill.click();
if (!document.getElementById("sourcesPanel").classList.contains("open")) {
  throw new Error("sources did not open before Escape");
}
let composerFocused = false;
box.focus = () => {
  composerFocused = true;
};
document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
if (!document.getElementById("modePop").hidden) throw new Error("Escape left the menu open");
if (document.getElementById("modeBtn").getAttribute("aria-expanded") !== "false") {
  throw new Error("Escape left the menu expanded");
}
if (document.getElementById("ioPanel").classList.contains("open")) {
  throw new Error("Escape left settings open");
}
if (document.getElementById("sourcesPanel").classList.contains("open")) {
  throw new Error("Escape left sources open");
}
if (!composerFocused) throw new Error("Escape did not focus the composer");

const beforeNew = document.body.textContent;
if (!beforeNew.includes("Blue light")) throw new Error("new chat had nothing to clear");
document.getElementById("btnNew").click();
if (document.body.textContent.includes("Blue light")) {
  throw new Error("new chat left the old reply");
}
if (!document.getElementById("empty")) throw new Error("new chat did not restore the empty state");
if (document.getElementById("brand").classList.contains("brand-title")) {
  throw new Error("new chat left the title up");
}

const idsBefore = sentHeaders.length;
box.value = "one more";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 40));
const posted = sentHeaders[sentHeaders.length - 1] || {};
const requestHeader = posted["X-Pi-Request-Id"] || "";
if (!requestHeader) throw new Error("chat did not send a request id");
if (sentHeaders.length !== idsBefore + 1) throw new Error("chat posted more than once");
const finished = streams[streams.length - 1];
finished.push({ choices: [{ delta: { content: "ok" } }] });
finished.end();
await new Promise((resolve) => setTimeout(resolve, 80));

box.value = "streaming now";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 40));
const streamingNow = streams[streams.length - 1];
box.value = "via enter";
box.dispatchEvent(new window.Event("input"));
if (box.disabled) throw new Error("composer disabled during a stream");
if (go.getAttribute("aria-label") !== "Send" || go.classList.contains("stop")) {
  throw new Error("send did not stay available while streaming");
}
const stopBtn = document.getElementById("stop");
if (!stopBtn || stopBtn.hidden) throw new Error("stop hid while a follow-up was waiting");
box.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await new Promise((resolve) => setTimeout(resolve, 20));
const enterChip = document.querySelector(".follow-chip");
if (!enterChip || !enterChip.textContent.includes("Queued") || !enterChip.textContent.includes("via enter")) {
  throw new Error("enter did not queue: " + (enterChip && enterChip.textContent));
}
if (box.value) throw new Error("composer kept the queued draft");
enterChip.querySelector("button").click();
if (document.querySelector(".follow-chip")) throw new Error("remove left the queued chip");

box.value = "after this";
box.dispatchEvent(new window.Event("input"));
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 20));
if (!document.querySelector(".follow-chip")?.textContent.includes("after this")) {
  throw new Error("send did not queue the follow-up");
}
const beforeDone = sentBodies.length;
streamingNow.push({ choices: [{ delta: { content: "done" } }] });
streamingNow.end();
await new Promise((resolve) => setTimeout(resolve, 80));
if (!sentBodies.slice(beforeDone).some((body) => body.includes("after this"))) {
  throw new Error("queue did not send when the reply finished");
}
if (document.querySelector(".follow-chip")) throw new Error("chip stayed after it sent");
const afterDone = streams[streams.length - 1];
afterDone.push({ choices: [{ delta: { content: "next" } }] });
afterDone.end();
await new Promise((resolve) => setTimeout(resolve, 40));

box.value = "stop me";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 40));
box.value = "still queued";
box.dispatchEvent(new window.Event("input"));
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 20));
const atStop = sentBodies.length;
if (go.getAttribute("aria-label") !== "Stop") throw new Error("stop did not return once the draft was queued");
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 80));
if (!sentBodies.slice(atStop).some((body) => body.includes("still queued"))) {
  throw new Error("stop did not send the queued follow-up");
}
if (document.querySelector(".follow-chip")) throw new Error("chip stayed after stop");
const afterStop = streams[streams.length - 1];
afterStop.push({ choices: [{ delta: { content: "stopped" } }] });
afterStop.end();
await new Promise((resolve) => setTimeout(resolve, 40));

box.value = "fresh stream";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 40));
box.value = "forget this";
box.dispatchEvent(new window.Event("input"));
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 20));
if (!document.querySelector(".follow-chip")) throw new Error("new-chat setup missed the chip");
document.getElementById("btnNew").click();
await new Promise((resolve) => setTimeout(resolve, 40));
if (document.querySelector(".follow-chip")) throw new Error("new chat left a queued message");
if (sentBodies.some((body) => body.includes("forget this"))) {
  throw new Error("new chat sent the queued message");
}

const realFetch = globalThis.fetch;
let blown = false;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (!blown && String(url).includes("/v1/chat/completions")) {
    blown = true;
    if (init && init.headers) sentHeaders.push(init.headers);
    throw new TypeError("Failed to fetch");
  }
  return realFetch(input, init);
};
const retryBefore = sentHeaders.length;
box.value = "retry once";
document.getElementById("go").click();
await new Promise((resolve) => setTimeout(resolve, 700));
globalThis.fetch = realFetch;
const retryHeaders = sentHeaders.slice(retryBefore);
if (retryHeaders.length !== 2) {
  throw new Error("a network miss did not retry once: " + retryHeaders.length);
}
const firstId = retryHeaders[0]["X-Pi-Request-Id"];
const secondId = retryHeaders[1]["X-Pi-Request-Id"];
if (!firstId || firstId !== secondId) throw new Error("retry did not reuse the request id");
if (firstId === requestHeader) throw new Error("a new turn reused the previous request id");

const ring = document.getElementById("btnMemory");
if (!ring || ring.textContent.trim() !== "") {
  throw new Error("memory ring still has a letter: " + JSON.stringify(ring && ring.textContent));
}
if (!/Context \d+% used/.test(ring.getAttribute("aria-label") || "")) {
  throw new Error("memory ring label was " + ring.getAttribute("aria-label"));
}
const meshFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  const method = (init && init.method) || "GET";
  if (String(url).includes("/v1/memory") && method === "GET") {
    return new Response(JSON.stringify({
      facts: [],
      summary: "",
      usage: { used: 512, num_ctx: 1024, compactions: 2 },
      compact_at: 0.7,
      compact_busy_at: 0.5,
    }), { status: 200, headers: { "content-type": "application/json" } });
  }
  return meshFetch(input, init);
};
const app = await import("../src/main.ts");
await app.refreshMemoryRing();
globalThis.fetch = meshFetch;
const offset = Number(ring.querySelector(".ring-fill").getAttribute("stroke-dashoffset"));
if (Math.abs(offset - 28.27) > 0.1) throw new Error("ring dashoffset was " + offset);

const pop = document.getElementById("memPop");
const memoryIds = [...pop.querySelectorAll("button")].map((node) => node.id);
if (memoryIds.join(",") !== "memCompact,memClear") {
  throw new Error("memory popup was " + memoryIds.join(","));
}
if (document.getElementById("memShow") || document.getElementById("memoryPanel")) {
  throw new Error("the extra memory panel is still on the page");
}
let memoryDeletes = 0;
const memoryFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  const method = ((init && init.method) || "GET").toUpperCase();
  if (String(url).includes("/v1/memory") && method === "DELETE") {
    memoryDeletes += 1;
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  }
  return memoryFetch(input, init);
};
document.getElementById("memClear").click();
await new Promise((resolve) => setTimeout(resolve, 20));
if (memoryDeletes !== 0) throw new Error("one tap cleared memory");
document.getElementById("memClear").click();
await new Promise((resolve) => setTimeout(resolve, 40));
if (memoryDeletes !== 1) throw new Error("two taps issued " + memoryDeletes + " deletes");
globalThis.fetch = memoryFetch;

class FakeXHR {
  constructor() {
    this.upload = {};
    this.status = 200;
    this.responseText = JSON.stringify({ text: "page one", route: "text" });
    this.onload = null;
    this.onerror = null;
    this.onabort = null;
    this.ontimeout = null;
    this.timeout = 0;
    this.aborted = false;
    FakeXHR.current = this;
  }
  open() {}
  send() {}
  abort() {
    this.aborted = true;
    if (typeof this.onabort === "function") this.onabort();
  }
}
globalThis.XMLHttpRequest = FakeXHR;
window.XMLHttpRequest = FakeXHR;
const pendingUpload = app.loadFile(new File(["abc"], "notes.pdf", { type: "application/pdf" }));
await new Promise((resolve) => setTimeout(resolve, 30));
const paperclip = document.getElementById("btnAttach");
if (!paperclip.classList.contains("live") || paperclip.getAttribute("aria-busy") !== "true" || !paperclip.disabled) {
  throw new Error("upload did not mark the paperclip busy");
}
document.getElementById("fileClear").click();
if (paperclip.classList.contains("live") || paperclip.getAttribute("aria-busy") || paperclip.disabled) {
  throw new Error("removing the file left the paperclip busy");
}
if (go.disabled) throw new Error("send stayed disabled after remove");
const fileTag = document.getElementById("fileTag");
if (fileTag.classList.contains("on")) throw new Error("the chip stayed on");
if (FakeXHR.current && typeof FakeXHR.current.onload === "function") FakeXHR.current.onload();
await pendingUpload.catch(() => {});
await new Promise((resolve) => setTimeout(resolve, 20));
if (fileTag.classList.contains("on")) throw new Error("a late upload restored the chip");
if (box.dataset.attachText) throw new Error("a ghost attachment was kept");

const reading = app.loadFile(new File(["abc"], "notes.pdf", { type: "application/pdf" }));
await new Promise((resolve) => setTimeout(resolve, 20));
if (typeof FakeXHR.current.upload.onload === "function") FakeXHR.current.upload.onload();
if (!document.getElementById("fileName").textContent.includes("reading")) {
  throw new Error("reading phase missing");
}
const readBar = document.getElementById("readBar");
if (!readBar || readBar.hidden || !readBar.classList.contains("on")) {
  throw new Error("read bar was not showing");
}
FakeXHR.current.ontimeout();
await reading;
if (paperclip.classList.contains("live") || paperclip.getAttribute("aria-busy") || paperclip.disabled) {
  throw new Error("timeout left the paperclip busy");
}
if (go.disabled) throw new Error("send stayed disabled after timeout");
if (!fileTag.classList.contains("err")) throw new Error("timeout did not mark the chip");
if (readBar.classList.contains("on") || !readBar.hidden) throw new Error("read bar stayed up");

const leaving = app.loadFile(new File(["abc"], "scan.pdf", { type: "application/pdf" }));
await new Promise((resolve) => setTimeout(resolve, 20));
window.dispatchEvent(new window.Event("pagehide"));
if (!FakeXHR.current.aborted) throw new Error("reload did not abort the upload");
if (paperclip.classList.contains("live") || paperclip.disabled || paperclip.getAttribute("aria-busy")) {
  throw new Error("reload left the paperclip busy");
}
if (readBar.classList.contains("on")) throw new Error("reload left the read bar up");
await leaving.catch(() => {});

const unloading = app.loadFile(new File(["abc"], "scan.pdf", { type: "application/pdf" }));
await new Promise((resolve) => setTimeout(resolve, 20));
window.dispatchEvent(new window.Event("unload"));
if (!FakeXHR.current.aborted) throw new Error("unload did not abort the upload");
if (paperclip.classList.contains("live") || paperclip.disabled) {
  throw new Error("unload left the paperclip busy");
}
await unloading.catch(() => {});

const restored = app.loadFile(new File(["abc"], "scan.pdf", { type: "application/pdf" }));
await new Promise((resolve) => setTimeout(resolve, 20));
const shown = new window.Event("pageshow");
shown.persisted = true;
window.dispatchEvent(shown);
if (!FakeXHR.current.aborted) throw new Error("bfcache restore did not abort the upload");
if (paperclip.classList.contains("live") || readBar.classList.contains("on")) {
  throw new Error("bfcache restore left the upload busy");
}
await restored.catch(() => {});

const busy = app.loadFile(new File(["abc"], "shot.jpg", { type: "image/jpeg" }));
await new Promise((resolve) => setTimeout(resolve, 20));
FakeXHR.current.status = 429;
FakeXHR.current.responseText = JSON.stringify({
  error: "Reading a file is busy. Try again in a moment.",
});
FakeXHR.current.onload();
await busy;
if (document.getElementById("voiceNote").textContent !== "Reading a file is busy. Try again in a moment.") {
  throw new Error("busy note was " + document.getElementById("voiceNote").textContent);
}
if (paperclip.classList.contains("live") || paperclip.disabled || paperclip.getAttribute("aria-busy")) {
  throw new Error("busy left the paperclip live");
}

const slow = app.loadFile(new File(["abc"], "scan.pdf", { type: "application/pdf" }));
await new Promise((resolve) => setTimeout(resolve, 20));
FakeXHR.current.status = 504;
FakeXHR.current.responseText = JSON.stringify({ error: "That took too long. Try again." });
FakeXHR.current.onload();
await slow;
if (document.getElementById("voiceNote").textContent !== "That took too long. Try again.") {
  throw new Error("slow note was " + document.getElementById("voiceNote").textContent);
}
if (paperclip.classList.contains("live")) throw new Error("slow left the paperclip live");

console.log("ok");
