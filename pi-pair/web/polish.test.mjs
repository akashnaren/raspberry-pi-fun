import { docExcerpt, modelUserContent } from "./src/attach.ts";
import { flowchartSvg } from "./src/diagram.ts";
import { renderMarkdown, renderStreamingMarkdown, stabilizeMarkdown } from "./src/markdown.ts";
import { suppressOfflineBanner } from "./src/presence.ts";
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

console.log("ok");
