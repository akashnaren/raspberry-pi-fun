import { test } from "node:test";
import assert from "node:assert/strict";
import { findHits, printReadyHtml, stripFindMarks } from "../src/docs-print.js";

test("printReadyHtml is a clean page a stranger can read off-screen", () => {
  const html = printReadyHtml(
    "Morning notes",
    "<h2>What ships</h2><ul><li>print</li></ul><table><tr><th>a</th><td>1</td></tr></table>",
  );
  assert.match(html, /<h1>Morning notes<\/h1>/);
  assert.match(html, /<h2>What ships<\/h2>/);
  assert.match(html, /<li>print<\/li>/);
  assert.match(html, /<table>/);
  assert.match(html, /border-collapse/);
  assert.doesNotMatch(html, /class="rail"|find-box|Meridian Desk/);
  assert.match(html, /max-width: 40rem/);
});

test("findHits and stripFindMarks keep headings out of the highlighter", () => {
  assert.deepEqual(findHits("H1 heading then H2 heading", "heading"), [
    { start: 3, end: 10 },
    { start: 19, end: 26 },
  ]);
  assert.deepEqual(findHits("nothing", "print"), []);
  assert.equal(stripFindMarks('Hello <mark class="find-hit">print</mark>'), "Hello print");
});
