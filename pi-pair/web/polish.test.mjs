import { docExcerpt, modelUserContent } from "./src/attach.ts";
import { flowchartSvg } from "./src/diagram.ts";
import { renderMarkdown, renderStreamingMarkdown, stabilizeMarkdown } from "./src/markdown.ts";
import { serviceView, shouldPollHealth, shouldSoftRetry, softRetryDelay, suppressOfflineBanner } from "./src/presence.ts";
import { tableFence } from "./src/table.ts";

const partial = "1. First\n2. Second\n```chart\n{\"title\":\"y\"}";
const stable = stabilizeMarkdown(partial);
if (!stable.trimEnd().endsWith("```")) throw new Error("an open fence was left open");
const streaming = renderStreamingMarkdown("## Heading\n\n- one\n- two");
if (!streaming.includes("<h2>") || !streaming.includes("<li>one</li>")) {
  throw new Error("streaming markdown stayed plain: " + streaming);
}
const broken = renderStreamingMarkdown("\\( x^2");
if (broken.includes("\\( x^2")) throw new Error("a dangling inline formula leaked: " + broken);

const dollars = renderMarkdown("The area is $A=\\pi r^2$ today.");
if (!dollars.includes('class="katex"')) throw new Error("single-dollar math was not typeset");
const money = renderMarkdown("It costs $5 today and $10 tomorrow.");
if (money.includes('class="katex"')) throw new Error("prices were typeset as math: " + money);

const table = tableFence("table", '{"title":"Years","columns":["Name","Year"],"rows":[["Dune","2021"]]}');
if (!table || !table.includes("<th>Name</th>") || !table.includes("<td>2021</td>")) {
  throw new Error("table fence was not a table: " + table);
}
const flow = flowchartSvg("flowchart TD\nA[Start] --> B{Ready}\nB -->|yes| C[Done]");
if (!flow || !flow.includes("Start") || !flow.includes("<svg")) throw new Error("flowchart was not drawn");

const shown = "What changed?";
const hidden = "The catalyst section is very long. ".repeat(80);
const packed = modelUserContent(shown, hidden);
if (!packed.startsWith(shown) || !packed.includes("\n\n---\n")) throw new Error("document was not separated");
if (docExcerpt(hidden).length > 141) throw new Error("preview dumped the document");

if (!suppressOfflineBanner(true, 0, 1000)) throw new Error("a hidden tab showed offline");
if (!suppressOfflineBanner(false, 1000, 1200)) throw new Error("a fresh resume showed offline");
if (suppressOfflineBanner(false, 1000, 5000)) throw new Error("a visible outage was hidden");
if (shouldPollHealth(true)) throw new Error("a hidden tab still polled health");
if (!shouldPollHealth(false)) throw new Error("a visible tab skipped health");
if (!shouldSoftRetry(0) || !shouldSoftRetry(1) || shouldSoftRetry(2)) {
  throw new Error("soft retries did not stop before the drop message");
}
if (softRetryDelay(0) < 200 || softRetryDelay(1) < softRetryDelay(0)) {
  throw new Error("soft retry did not wait");
}
const view = serviceView({
  uptime_s: 3720,
  services: {
    brain: { ok: true, latency_ms: 18 },
    search: { ok: false, latency_ms: null },
    peers_up: 2,
    peers: 3,
  },
});
if (view.now !== "Up 1h 2m · Chat up 18ms · Search down · Fleet 2/3") {
  throw new Error("uptime line was " + view.now);
}
const later = serviceView({
  uptime_s: 3780,
  services: {
    brain: { ok: true, latency_ms: 18 },
    search: { ok: false },
    peers_up: 2,
    peers: 3,
  },
});
if (later.signature !== view.signature) throw new Error("uptime ticks rewrote the status log");
if (!later.now.startsWith("Up 1h 3m")) throw new Error("live uptime did not move");

console.log("ok");
