import fs from "fs";
import { parseHTML } from "linkedom";
import { docExcerpt, modelUserContent, userMessagePieces } from "../src/attach/message.ts";
import { mountCharts } from "../src/render/chart.ts";
import { flowchartSvg, mountDiagrams } from "../src/render/diagram.ts";
import { renderMarkdown, renderStreamingMarkdown, stabilizeMarkdown } from "../src/render/markdown.ts";
import { linkCitations } from "../src/chat/sources.ts";
import { serviceView, shouldPollHealth, shouldSoftRetry, softRetryDelay, suppressOfflineBanner } from "../src/core/presence.ts";
const partial = "1. First\n2. Second\n```chart\n{\"title\":\"y\"}";
const stable = stabilizeMarkdown(partial);
if (!stable.trimEnd().endsWith("```")) throw new Error("an open fence was left open");
const streaming = renderStreamingMarkdown("## Heading\n\n- one\n- two");
if (!streaming.includes("<h2>") || !streaming.includes("<li>one</li>")) {
  throw new Error("streaming markdown stayed plain: " + streaming);
}
const streamingBold = renderStreamingMarkdown("a **b");
if (!streamingBold.includes("<strong>b</strong>")) {
  throw new Error("a dangling bold marker stayed literal: " + streamingBold);
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

const coded = renderMarkdown("```python\nprint(1)\n```");
if (!coded.includes('class="language-python"') || !coded.includes("print(1)")) {
  throw new Error("a code fence lost its language tag: " + coded);
}
const cited = linkCitations("See [1] and [9].", 1);
if (!cited.includes('href="#pi-src-1"') || !cited.includes("[9]")) {
  throw new Error("citations were not linked from the snippets: " + cited);
}
const tableChart = renderMarkdown("| item | value |\n| --- | --- |\n| a | 1 |\n| b | 2 |\n");
if (!tableChart.includes("<table>") || !tableChart.includes('class="pi-chart"')) {
  throw new Error("a data table did not keep the table and a chart: " + tableChart);
}
const plot = renderMarkdown("```plot\ntitle: Wave\nsin(x)\n```");
if (!plot.includes('class="pi-chart"') || !plot.includes("sin(x)")) {
  throw new Error("a plot fence was not kept: " + plot);
}
const numbered = renderMarkdown("1. Alpha\n\n2. Beta\n\n3. Gamma");
const numbers = [...numbered.matchAll(/<li value="(\d+)">/g)].map((match) => match[1]);
if (numbers.join(",") !== "1,2,3" || (numbered.match(/<ol>/g) || []).length !== 1) {
  throw new Error("numbered list reset: " + numbered);
}
const docCard = renderMarkdown("```doc\nkind: txt\ntitle: Note\nHello\n```");
const untyped = renderMarkdown("```doc\nHello there\n```");
if (untyped.includes(".docx") || !untyped.includes(".md")) {
  throw new Error("an untyped doc fell back to docx: " + untyped);
}
if (!docCard.includes("pi-doc") || !docCard.includes("Download")) {
  throw new Error("a doc fence had no download card: " + docCard);
}
const twice = renderMarkdown(
  "```doc\nkind: docx\ntitle: Note\nHello\n```\n```doc\nkind: docx\ntitle: Note\nHello\n```",
);
if (twice.split("pi-doc").length - 1 !== 1) {
  throw new Error("a repeated doc fence rendered twice: " + twice);
}
const pdfCard = renderMarkdown("```pdf\nQuarter notes\n```");
if (!pdfCard.includes("pi-doc") || !pdfCard.includes(".pdf")) {
  throw new Error("a pdf fence had no download: " + pdfCard);
}
const sheet = renderMarkdown("```xlsx\n| a | b |\n| --- | --- |\n| 1 | 2 |\n```");
if (!sheet.includes("pi-doc") || !sheet.includes(".xlsx")) {
  throw new Error("an xlsx fence had no download: " + sheet);
}
const emptyFence = renderMarkdown("Hello\n```calc\n```\nthere");
if (emptyFence.includes("<pre>") || emptyFence.includes("```")) {
  throw new Error("an empty fence stayed in the reply: " + emptyFence);
}
const tablePlot = renderMarkdown("```chart\n| item | n |\n| --- | --- |\n| a | 1 |\n```");
if (!tablePlot.includes('class="pi-chart"')) {
  throw new Error("a chart table was not drawn: " + tablePlot);
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
if (!suppressOfflineBanner(false, 0, 5000, 2500, true)) {
  throw new Error("a live reply showed the offline banner");
}
if (suppressOfflineBanner(false, 0, 5000, 2500, false)) {
  throw new Error("an idle outage stayed hidden");
}
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

const page = fs.readFileSync(new URL("../public/index.html", import.meta.url), "utf8");
const builtPage = fs.readFileSync(new URL("../../static/index.html", import.meta.url), "utf8");
const builtCss = fs.readFileSync(new URL("../../static/mesh.css", import.meta.url), "utf8");
if (page.includes("voice-dots") || builtPage.includes("voice-dots")) {
  throw new Error("the page still has the five voice dots");
}
const reducedAt = builtCss.indexOf("prefers-reduced-motion");
if (reducedAt < 0 || !builtCss.slice(reducedAt, reducedAt + 800).includes("#voiceStage")) {
  throw new Error("reduced motion does not cover the voice stage");
}

const plotHtml = renderMarkdown("```plot\ntitle: Wave\nsin(x)\n```");
const plotHost = parseHTML(
  `<div id="one">${plotHtml}</div><div id="two">${plotHtml}</div>`,
);
const previousWindow = globalThis.window;
const previousDocument = globalThis.document;
const previousFetch = globalThis.fetch;
let chartFetches = 0;
plotHost.window.Plotly = { newPlot() {} };
globalThis.window = plotHost.window;
globalThis.document = plotHost.document;
globalThis.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  if (String(url).includes("/tools/render_chart")) {
    chartFetches += 1;
    return new Response(
      JSON.stringify({
        ok: true,
        figure: { data: [{ type: "scatter", x: [0, 1], y: [0, 1] }], layout: {} },
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }
  if (previousFetch) return previousFetch(input, init);
  return new Response("missing", { status: 404 });
};
mountCharts(plotHost.document.getElementById("one"));
mountCharts(plotHost.document.getElementById("two"));
await new Promise((resolve) => setTimeout(resolve, 40));
globalThis.window = previousWindow;
globalThis.document = previousDocument;
globalThis.fetch = previousFetch;
if (chartFetches !== 1) {
  throw new Error("the same plot fetched render_chart " + chartFetches + " times");
}

console.log("ok");
