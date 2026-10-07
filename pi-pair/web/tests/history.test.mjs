import fs from "fs";
import { register } from "node:module";

await register("./support/ts-resolve.mjs", import.meta.url);
const { chatBody } = await import("../src/history.ts");

const options = {
  model: "flash",
  effort: "medium",
  mode: "auto",
  sys: "Be brief.",
};

const turns = [
  { role: "user", content: "hello", hidden: "" },
  { role: "assistant", content: "Hello there." },
  { role: "assistant", content: "Hello there.", stopped: true },
  { role: "user", content: "hello there", echo: true },
  { role: "assistant", content: "Hello there." },
  { role: "assistant", content: "Hello there." },
  { role: "user", content: "Where is the bench?", hidden: "page 2" },
];

const spoken = chatBody(turns, { ...options, spoken: true });
const typed = chatBody(turns, { ...options, spoken: false });
if (JSON.stringify(spoken) !== JSON.stringify(typed)) {
  throw new Error("spoken changed the chat body");
}
const roles = spoken.messages.map((row) => row.role + ":" + row.content);
if (roles[0] !== "system:Be brief.") throw new Error("system row missing: " + roles.join(" | "));
const assistants = roles.filter((row) => row.startsWith("assistant:"));
if (assistants.length !== 1 || assistants[0] !== "assistant:Hello there.") {
  throw new Error("assistant hygiene failed: " + roles.join(" | "));
}
if (!roles.includes("user:Where is the bench?\n\n---\npage 2")) {
  throw new Error("the real question was dropped: " + roles.join(" | "));
}
if (spoken.messages.some((row) => row.content === "hello there")) {
  throw new Error("echo user stayed in the body");
}

const fresh = chatBody(turns, { ...options, fresh: true });
const freshRoles = fresh.messages.map((row) => row.role);
if (freshRoles.join(",") !== "system,user") {
  throw new Error("fresh body was " + freshRoles.join(","));
}
if (!fresh.messages[1].content.startsWith("Where is the bench?")) {
  throw new Error("fresh body lost the last user");
}

const fixture = JSON.parse(fs.readFileSync(new URL("./fixtures/voice-greeting-loop.json", import.meta.url), "utf8"));
const replay = chatBody(fixture.turns, { model: "flash", effort: "medium", mode: "auto" });
const greeting = "I'm here to help with anything you need. How can I assist you today?";
const copies = replay.messages.filter((row) => row.content === greeting);
if (copies.length !== 1) throw new Error("greeting duplicates left in the replay: " + copies.length);
if (replay.messages.some((row) => row.content === "I'm here to help with anything you need.")) {
  throw new Error("a stopped partial was sent");
}
if (replay.messages.some((row) => row.content === "anything you need")) {
  throw new Error("an echo user was sent");
}
const last = replay.messages[replay.messages.length - 1];
if (!last || last.role !== "user" || last.content !== "What is the top electric car to buy") {
  throw new Error("replay did not end on the real question: " + JSON.stringify(last));
}

const { parseHTML } = await import("linkedom");
const page = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const dom = parseHTML(page);
const { document, window } = dom;
const memory = new Map();
function memStore() {
  return {
    getItem(key) { return memory.has(key) ? memory.get(key) : null; },
    setItem(key, value) { memory.set(key, String(value)); },
    removeItem(key) { memory.delete(key); },
  };
}
const store = memStore();
const answer = "The tower stands in Paris beside the river and was built for a world fair in the late nineteenth century.";
store.setItem(
  "openpi.transcript",
  JSON.stringify([
    { role: "user", content: "Tell me about the Eiffel Tower" },
    { role: "assistant", content: answer },
  ]),
);
window.sessionStorage = store;
window.localStorage = memStore();
window.confirm = () => true;
window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
window.matchMedia = () => ({
  matches: false,
  media: "",
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return false; },
});
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
const imageUrls = [];
globalThis.fetch = async (input) => {
  const url = typeof input === "string" ? input : input.url;
  imageUrls.push(String(url));
  if (String(url).includes("/health")) {
    return new Response(JSON.stringify({ peers: [], modes: {} }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  if (String(url).includes("/v1/memory")) {
    return new Response(JSON.stringify({ facts: [], usage: { used: 0, num_ctx: 2048, compactions: 0 } }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }
  return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
};
await import("../src/main.ts");
await new Promise((resolve) => setTimeout(resolve, 40));
if (imageUrls.some((url) => url.includes("/v1/images"))) {
  throw new Error("restore fetched images: " + imageUrls.join(" "));
}
if (!document.body.textContent.includes("world fair")) {
  throw new Error("the restored reply was not painted");
}

console.log("ok");
