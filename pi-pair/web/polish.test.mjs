import { parseHTML } from "linkedom";
import { docExcerpt, modelUserContent, userMessagePieces } from "./src/attach.ts";
import { flowchartSvg, mountDiagrams } from "./src/diagram.ts";
import { renderMarkdown, renderStreamingMarkdown, stabilizeMarkdown } from "./src/markdown.ts";
import { serviceView, shouldPollHealth, shouldSoftRetry, softRetryDelay, suppressOfflineBanner } from "./src/presence.ts";
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

const flow = flowchartSvg("flowchart TD\nA[Start] --> B{Ready}\nB -->|yes| C[Done]");
if (!flow || !flow.includes("Start") || !flow.includes("<svg")) throw new Error("flowchart was not drawn");

const shown = "What changed?";
const hidden = "The catalyst section is very long. ".repeat(80);
const packed = modelUserContent(shown, hidden);
if (!packed.startsWith(shown) || !packed.includes("\n\n---\n")) throw new Error("document was not separated");
if (docExcerpt(hidden).length > 141) throw new Error("preview dumped the document");
const pieces = userMessagePieces(shown, {
  name: "notes.pdf",
  route: "ocr",
  bytes: 12000,
  excerpt: hidden,
});
if (pieces[0]?.kind !== "card" || pieces[1]?.text !== shown) {
  throw new Error("doc card was not above the question");
}
if (pieces[0].text.length > 141 || pieces.some((piece) => piece.text.includes(hidden))) {
  throw new Error("the bubble dumped the document");
}

const gfm = renderMarkdown("| Name | Year |\n| --- | ---: |\n| Dune | 2021 |");
if (!gfm.includes("<table>") || !gfm.includes("<th>Name</th>") || !gfm.includes("<td>2021</td>")) {
  throw new Error("GFM table stayed prose: " + gfm);
}
const wrappedMd = renderMarkdown("```markdown\n| Name | Year |\n| --- | --- |\n| Dune | 2021 |\n```");
if (!wrappedMd.includes("<table>") || !wrappedMd.includes("<th>Name</th>") || wrappedMd.includes("<pre>")) {
  throw new Error("a sole markdown fence stayed a code block: " + wrappedMd);
}
const wrappedAlias = renderMarkdown("```md\n| Fruit | Count |\n| --- | --- |\n| Apple | 2 |\n```");
if (!wrappedAlias.includes("<td>Apple</td>") || wrappedAlias.includes("<pre>")) {
  throw new Error("a sole md fence stayed a code block: " + wrappedAlias);
}
const beside = renderMarkdown("Intro\n\n```markdown\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```");
if (beside.includes("<table>") || !beside.includes("<pre>")) {
  throw new Error("a markdown fence beside prose was unwrapped: " + beside);
}
const bare = renderMarkdown("Name | Year\n--- | ---\nDune | 2021");
if (!bare.includes("<th>Year</th>") || !bare.includes("<td>Dune</td>")) {
  throw new Error("bare GFM table stayed prose: " + bare);
}

const chart = renderMarkdown(
  '```chart\n{"title":"y = x^2","data":[{"type":"scatter","mode":"lines","y":[0,1,4]}]}\n```',
);
if (chart.includes('class="pi-chart"') || chart.includes("plotly") || !chart.includes("<pre>")) {
  throw new Error("a chart fence was drawn instead of left as code: " + chart);
}
const jsonTable = renderMarkdown(
  '```table\n{"title":"Years","columns":["Name","Year"],"rows":[["Dune","2021"]]}\n```',
);
if (jsonTable.includes("<table>") || !jsonTable.includes("<pre>")) {
  throw new Error("a json table fence was drawn: " + jsonTable);
}

const pending = renderMarkdown("```mermaid\nflowchart TD\nA[Start] --> B[Done]\n```");
if (pending.includes("<svg")) throw new Error("flow was drawn before mount");
if (!pending.includes("pi-diagram")) throw new Error("flow placeholder missing");
const host = parseHTML(`<div id="host">${pending}</div>`).document.getElementById("host");
mountDiagrams(host);
if (!host.innerHTML.includes("<svg") || !host.innerHTML.includes("Start")) {
  throw new Error("lazy flow did not mount: " + host.innerHTML);
}
if (renderMarkdown("No diagram here.").includes("pi-diagram")) {
  throw new Error("plain text grew a diagram");
}

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
