import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("Meridian Office suite: Docs cream flagship; Sheets grid; Slides deck; Timezone Buddy catalogue", async () => {
  const docs = await readFile(join(REPO, "workspace/product/index.html"), "utf8");
  const sheets = await readFile(join(REPO, "workspace/product/sheets.html"), "utf8");
  const slides = await readFile(join(REPO, "workspace/product/slides.html"), "utf8");
  const buddy = await readFile(join(REPO, "workspace/product/timezone-buddy.html"), "utf8");
  assert.equal(syntaxCheckHtml(docs).ok, true);
  assert.equal(syntaxCheckHtml(sheets).ok, true);
  assert.equal(syntaxCheckHtml(slides).ok, true);
  assert.equal(syntaxCheckHtml(buddy).ok, true);

  assert.match(docs, /Meridian Office/);
  assert.match(docs, /contenteditable/);
  assert.match(docs, /download-md/);
  assert.match(docs, /download-html/);
  assert.match(docs, /localStorage/);
  assert.match(docs, /\.md/);
  assert.match(docs, /Morning notes/);
  assert.match(docs, /id="kept"/);
  assert.match(docs, /id="save-md"/);
  assert.match(docs, /function downloadMarkdown/);
  assert.match(docs, /Ctrl\+S downloads \.md/);
  assert.match(docs, /Kept locally/);
  assert.match(docs, /#f4ecd8|#f3ead7/);
  assert.doesNotMatch(docs, /AI assistant|auto-?write|generate copy|magic wand/i);
  const formatCmds = (docs.match(/data-cmd=|data-heading=/g) || []).length;
  assert.ok(formatCmds <= 4, "toolbar stays four commands");

  assert.match(sheets, /aria-current="page">Sheets/);
  assert.match(sheets, /localStorage/);
  assert.match(sheets, /download-csv/);
  assert.match(sheets, /id="paste-from"/);
  assert.match(sheets, /function parseDelimited/);
  assert.match(sheets, /function applyPaste/);
  assert.match(sheets, /function toCsv/);
  assert.match(sheets, /Paste fills from the selected cell/);
  assert.match(sheets, /Tab/);
  assert.match(sheets, /Enter/);
  assert.match(sheets, /COLS = 8/);
  assert.match(sheets, /ROWS = 12/);
  assert.doesNotMatch(sheets, /SUM\(|AI assistant|=A1\+/i);
  assert.match(sheets, /#f4ecd8|#f3ead7|#faf4e8/);

  assert.match(slides, /aria-current="page">Slides/);
  assert.match(slides, /localStorage/);
  assert.match(slides, /download-md/);
  assert.match(slides, /download-html/);
  assert.match(slides, /id="slide-title"/);
  assert.match(slides, /id="slide-body"/);
  assert.match(slides, /add slide/);
  assert.match(slides, /ArrowLeft/);
  assert.match(slides, /No animation/);
  assert.match(slides, /Meridian Office/);
  assert.doesNotMatch(slides, /Ken Burns|transition: *all|animation-engine/i);

  assert.match(buddy, /Timezone Buddy/);
  assert.match(buddy, /catalogue/);
  assert.doesNotMatch(docs, /timezone-buddy\.html/);
  assert.doesNotMatch(sheets, /timezone-buddy\.html/);
  assert.doesNotMatch(slides, /timezone-buddy\.html/);
  for (const page of [docs, sheets, slides, buddy]) {
    assert.doesNotMatch(page, /sign[- ]?up|create an account|checkout|stripe|upload a file|chat box|live chat/i);
  }
});

test("board seeds useful tools and keeps DRY_RUN", async () => {
  const backlog = JSON.parse(await readFile(join(REPO, "workspace/backlog.json"), "utf8"));
  const texts = backlog.tasks.map((task) => task.text).join("\n");
  assert.match(texts, /Paste-to-CSV|paste a grid/i);
  assert.match(texts, /Invoice/);
  assert.match(texts, /kanban/);
  assert.match(texts, /Markdown/);
  assert.match(texts, /Timezone Buddy/);
  const board = await readFile(join(REPO, "workspace/board/inbox.md"), "utf8");
  assert.match(board, /Meridian Office/);
  assert.match(board, /DRY_RUN/);
  const env = await readFile(join(REPO, ".env.example"), "utf8");
  assert.match(env, /DRY_RUN=true/);
});
