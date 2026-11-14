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
if (readWebSearch(null) !== true) throw new Error("missing storage defaulted off");
if (readWebSearch(empty) !== true) throw new Error("new session defaulted off");
writeWebSearch(empty, true);
if (empty.getItem(WEB_SEARCH_KEY) !== "1" || readWebSearch(empty) !== true) {
  throw new Error("session did not round-trip on");
}
writeWebSearch(empty, false);
if (empty.getItem(WEB_SEARCH_KEY) !== "0" || readWebSearch(empty) !== false) {
  throw new Error("session did not keep an explicit off");
}
empty.setItem(WEB_SEARCH_KEY, "0");
if (readWebSearch(empty) !== false) throw new Error("stored off was ignored");

const turns = [{ role: "user", content: "hi" }];
const base = { model: "flash", effort: "medium", mode: "auto" };
const omitted = chatBody(turns, base);
const on = chatBody(turns, { ...base, webSearch: true });
const off = chatBody(turns, { ...base, webSearch: false });
const spoken = chatBody(turns, { ...base, spoken: true });
if (omitted.web_search !== true) throw new Error("chat default left search off");
if (on.web_search !== true) throw new Error("chat did not force search");
if (off.web_search !== false) throw new Error("explicit off still searched");
if (JSON.stringify(omitted) !== JSON.stringify(spoken)) throw new Error("spoken changed web_search");

const html = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const searchButton = html.match(/<button[^>]*id="webSearch"[^>]*>/);
if (!searchButton) throw new Error("composer has no search control");
if (!html.includes('aria-label="Search the web"')) throw new Error("search control has no accessible name");
if (!/id="webSearch"[\s\S]*Search\s*</.test(html)) throw new Error("search control has no visible label");
if (!searchButton[0].includes('aria-pressed="true"') || !searchButton[0].includes("search-btn on")) {
  throw new Error("search control does not start on");
}

const settings = fs.readFileSync(new URL("../src/core/settings.ts", import.meta.url), "utf8");
if (settings.includes("webSearch") || settings.includes("web_search")) {
  throw new Error("search toggle was stored with page settings");
}

const scss = fs.readFileSync(new URL("../src/styles/main.scss", import.meta.url), "utf8");
if (!scss.includes(".composer-group") || !scss.includes(".search-btn") || !scss.includes(".composer-options")) {
  throw new Error("search control has no style");
}
if (/<div class="composer-group"[\s\S]*id="webSearch"/.test(html)) {
  throw new Error("search is still inside the combined composer pill");
}
if (/rgba\(126,\s*200,\s*184/.test(scss.slice(scss.indexOf(".search-btn"), scss.indexOf(".search-btn") + 500))) {
  throw new Error("search still uses the teal fill");
}
