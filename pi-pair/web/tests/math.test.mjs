import { renderMarkdown } from "../src/markdown.ts";

const source = [
  "Let \\( r \\) be the radius and \\( A \\) the area.",
  "",
  "\\[",
  "A = 4\\pi r^{2}",
  "\\]",
  "",
  "\\[",
  "\\frac{d}{dt}(A) = 8\\pi r \\frac{dr}{dt}",
  "\\]",
].join("\n");

const html = renderMarkdown(source);
if (!html.includes('class="katex"')) {
  throw new Error("formula was not rendered");
}
if (!html.includes("katex-display")) {
  throw new Error("display formula was not rendered");
}
if (html.includes("\\( r \\)") || html.includes("\\[") || html.includes("\\frac{d}{dt}(A)")) {
  throw new Error("backslash source is still in the markup: " + html.slice(0, 400));
}

const coded = renderMarkdown("Keep `\\( r \\)` as code.");
if (!coded.includes("<code>")) {
  throw new Error("code span missing");
}
if (coded.includes('class="katex"')) {
  throw new Error("code span was typeset");
}

const linked = renderMarkdown("$$\\href{javascript:alert(1)}{click}$$");
if (/href\s*=\s*["']?\s*javascript:/i.test(linked)) {
  throw new Error("katex trusted a javascript href: " + linked.slice(0, 500));
}
if (!linked.includes('class="katex"')) {
  throw new Error("href formula was not rendered");
}

const mermaid = renderMarkdown("```mermaid\nflowchart TD\nA[<script>alert(1)</script>]\n```");
if (mermaid.includes("<script>") || mermaid.includes("<svg")) {
  throw new Error("mermaid was executed: " + mermaid);
}
if (!mermaid.includes("&lt;script&gt;") || !mermaid.includes('class="language-mermaid"')) {
  throw new Error("mermaid was not escaped: " + mermaid);
}

console.log("ok");
