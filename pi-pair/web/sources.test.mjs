import { parseHTML } from "linkedom";
import {
  faviconPlan,
  faviconStack,
  renderFailedSearch,
  renderSourcesPanelBody,
  renderSourcesPill,
  sourceCountLabel,
  sourceHost,
  validSources,
} from "./src/sources.ts";

const links = [
  { title: "Bench note", url: "https://example.com/bench" },
  { title: "Window", url: "https://news.example.org/window" },
  { title: "Orbit", url: "https://www.orbit.test/path" },
  { title: "Extra", url: "https://extra.test/a" },
  { title: "Same host", url: "https://example.com/other" },
  { title: "Skip", url: "not-a-url" },
];

if (sourceCountLabel(validSources(links).length) !== "5 sources") {
  throw new Error("count was " + sourceCountLabel(validSources(links).length));
}
if (sourceCountLabel(1) !== "1 source") throw new Error("singular count");
if (sourceHost("https://www.orbit.test/path") !== "orbit.test") throw new Error("host");

const stack = faviconStack(links);
if (stack.length !== 3) throw new Error("favicon stack was " + stack.length);
if (stack.some((item) => item.url.includes("example.com/other"))) {
  throw new Error("stack repeated a host");
}
const plan = faviconPlan(stack[0].url);
if (!plan.google.includes("www.google.com/s2/favicons") || !plan.google.includes("example.com")) {
  throw new Error("google favicon url " + plan.google);
}
if (plan.local !== "https://example.com/favicon.ico") throw new Error("local favicon " + plan.local);
if (plan.letter !== "E") throw new Error("letter " + plan.letter);

const { document } = parseHTML("<!doctype html><body></body>");
const pill = renderSourcesPill(document, links);
document.body.appendChild(pill);
if (pill.querySelector(".sources-count").textContent !== "5 sources") {
  throw new Error("pill count " + pill.querySelector(".sources-count").textContent);
}
const icons = pill.querySelectorAll(".sources-fav img");
if (icons.length !== 3) throw new Error("pill icons " + icons.length);
if (pill.querySelectorAll(".sources-fallback").length !== 3) {
  throw new Error("favicon discs missing a letter fallback");
}
if (!icons[0].src.includes("google.com/s2/favicons") || icons[0].dataset.local !== plan.local) {
  throw new Error("pill favicon " + icons[0].src);
}

const failed = renderFailedSearch(document);
if (failed.textContent !== "Search failed" || !failed.classList.contains("search-failed")) {
  throw new Error("failed state is not readable");
}

const panel = renderSourcesPanelBody(document, {
  status: "ok",
  sources: links,
  stages: ["thinking", "searching", "answering"],
  prompt: "Where is the bench?",
});
document.body.appendChild(panel);
if (!panel.textContent.includes("Thinking")) throw new Error("panel skipped thinking");
if (!panel.textContent.includes("Searched web")) throw new Error("panel skipped search");
const badge = panel.querySelector(".sources-badge");
if (!badge || badge.textContent !== "5") throw new Error("panel count " + (badge && badge.textContent));
const anchors = [...panel.querySelectorAll(".sources-links a")];
if (anchors.length !== 5) throw new Error("panel links " + anchors.length);
if (anchors[0].querySelector(".sources-title").textContent !== "Bench note") {
  throw new Error("panel title");
}
if (anchors[2].querySelector(".sources-host").textContent !== "orbit.test") {
  throw new Error("panel host " + anchors[2].querySelector(".sources-host").textContent);
}
if (anchors.some((node) => node.getAttribute("href") === "not-a-url")) {
  throw new Error("panel kept a bad url");
}

const hostile = [
  { title: "js", url: "javascript:alert(1)" },
  { title: "data", url: "data:text/html,hi" },
  { title: "proto", url: "//evil.example/a" },
  { title: "prefix", url: "http-not-a-url" },
  { title: "creds", url: "https://user:pass@example.com/a" },
  { title: "ok", url: "http://example.com/plain" },
];
const kept = validSources(hostile);
if (kept.length !== 1 || kept[0].url !== "http://example.com/plain") {
  throw new Error("hostile sources leaked " + JSON.stringify(kept));
}
const hostilePanel = renderSourcesPanelBody(document, {
  status: "ok",
  sources: hostile,
  stages: ["searching"],
  prompt: "x",
});
const hrefs = [...hostilePanel.querySelectorAll("a")].map((node) => node.getAttribute("href"));
if (hrefs.length !== 1 || hrefs[0] !== "http://example.com/plain") {
  throw new Error("panel href " + hrefs.join(","));
}
if (hrefs.some((href) => /javascript:|data:/i.test(href))) {
  throw new Error("script href " + hrefs.join(","));
}

console.log("ok");
