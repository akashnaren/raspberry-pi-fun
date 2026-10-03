import { renderMarkdown } from "./src/markdown.ts";

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
if (html.includes("\\( r \\))") || html.includes("\\[") || html.includes("\\frac{d}{dt}(A)")) {
  throw new Error("backslash source is still in the markup: " + html.slice(0, 400));
}

const coded = renderMarkdown("Keep `\\( r \\)` as code.");
if (!coded.includes("<code>")) {
  throw new Error("code span missing");
}
if (coded.includes('class="katex"')) {
  throw new Error("code span was typeset");
}

console.log("ok");
