import fs from "fs";
import { register } from "node:module";

await register("./support/ts-resolve.mjs", import.meta.url);
const { WEB_SEARCH_KEY, readWebSearch, writeWebSearch } = await import("../src/chat/web-search.ts");
const { chatBody } = await import("../src/chat/history.ts");

function mem() {
  const memory = new Map();
  return {
    getItem(key) { return memory.has(key) ? memory.get(key) : null; },
    setItem(key, value) { memory.set(key, String(value)); },
    removeItem(key) { memory.delete(key); },
  };
}

const empty = mem();
if (readWebSearch(null) !== false) throw new Error("missing storage searched");
if (readWebSearch(empty) !== false) throw new Error("default was on");
writeWebSearch(empty, true);
if (empty.getItem(WEB_SEARCH_KEY) !== "1" || readWebSearch(empty) !== true) {
  throw new Error("session did not round-trip on");
}
writeWebSearch(empty, false);
if (empty.getItem(WEB_SEARCH_KEY) !== "0" || readWebSearch(empty) !== false) {
  throw new Error("session did not round-trip off");
}

const turns = [{ role: "user", content: "hi" }];
const base = { model: "flash", effort: "medium", mode: "auto" };
const off = chatBody(turns, base);
const on = chatBody(turns, { ...base, webSearch: true });
const spoken = chatBody(turns, { ...base, spoken: true });
if (off.web_search !== false) throw new Error("chat default forced search");
if (on.web_search !== true) throw new Error("chat did not force search");
if (JSON.stringify(off) !== JSON.stringify(spoken)) throw new Error("spoken changed web_search");

const html = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
if (!html.includes('id="webSearch"')) throw new Error("composer has no search control");
if (!html.includes('aria-label="Search the web"')) throw new Error("search control has no accessible name");
if (!html.includes(">Search</button>")) throw new Error("search control has no visible label");
if (!html.includes('aria-pressed="false"')) throw new Error("search control does not start off");

const settings = fs.readFileSync(new URL("../src/core/settings.ts", import.meta.url), "utf8");
if (settings.includes("webSearch") || settings.includes("web_search")) {
  throw new Error("search toggle was stored with page settings");
}

const scss = fs.readFileSync(new URL("../src/styles/main.scss", import.meta.url), "utf8");
const modesAt = scss.indexOf(".composer-modes {");
const modes = scss.slice(modesAt, modesAt + 160);
if (/overflow\s*:/.test(modes)) throw new Error("mode row clips the popup");
if (!scss.includes(".search-btn")) throw new Error("search control has no style");
