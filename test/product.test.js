import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("Meridian Office suite: cream rail, Docs leap, catalogue utilities", async () => {
  const docs = await readFile(join(REPO, "workspace/product/index.html"), "utf8");
  const sheets = await readFile(join(REPO, "workspace/product/sheets.html"), "utf8");
  const slides = await readFile(join(REPO, "workspace/product/slides.html"), "utf8");
  const buddy = await readFile(join(REPO, "workspace/product/timezone-buddy.html"), "utf8");
  const paste = await readFile(join(REPO, "workspace/product/paste-csv.html"), "utf8");
  const invoice = await readFile(join(REPO, "workspace/product/invoice.html"), "utf8");
  const notes = await readFile(join(REPO, "workspace/product/meeting-notes.html"), "utf8");
  assert.equal(syntaxCheckHtml(docs).ok, true);
  assert.equal(syntaxCheckHtml(sheets).ok, true);
  assert.equal(syntaxCheckHtml(slides).ok, true);
  assert.equal(syntaxCheckHtml(buddy).ok, true);
  assert.equal(syntaxCheckHtml(paste).ok, true);
  assert.equal(syntaxCheckHtml(invoice).ok, true);
  assert.equal(syntaxCheckHtml(notes).ok, true);

  for (const page of [docs, sheets, slides, paste, buddy, invoice, notes]) {
    assert.match(page, /class="rail"/);
    assert.match(page, /class="apps"/);
    assert.match(page, /Meridian Office/);
    assert.doesNotMatch(page, /Meridian Desk/);
    assert.match(page, /#f4ecd8|#efe6d2|#faf4e8/);
    assert.doesNotMatch(page, /sign[- ]?up|create an account|checkout|stripe|upload a file|chat box|live chat/i);
  }

  const docsRail = docs.match(/class="apps"[\s\S]*?<\/nav>/)[0];
  assert.match(docsRail, /Docs/);
  assert.match(docsRail, /Sheets/);
  assert.match(docsRail, /Slides/);
  assert.doesNotMatch(docsRail, /Timezone|paste-csv|Invoice|Notes/i);
  assert.match(docs, /aria-label="Catalogue"/);
  assert.match(docs, /timezone-buddy\.html/);
  assert.match(docs, /paste-csv\.html/);
  assert.match(docs, /invoice\.html/);
  assert.match(docs, /meeting-notes\.html/);

  assert.match(docs, /aria-current="page">Docs/);
  assert.match(docs, /contenteditable/);
  assert.match(docs, /data-heading="1"/);
  assert.match(docs, /data-heading="2"/);
  assert.match(docs, /insertUnorderedList/);
  assert.match(docs, /insertOrderedList/);
  assert.match(docs, /id="find-box"/);
  assert.match(docs, /id="find-q"/);
  assert.match(docs, /function printReadyHtml/);
  assert.match(docs, /function downloadMarkdown/);
  assert.match(docs, /<h1>/);
  assert.match(docs, /<h2>/);
  assert.match(docs, /<ul>/);
  assert.match(docs, /<ol>/);
  assert.match(docs, /localStorage/);
  assert.match(docs, /download-md/);
  assert.match(docs, /download-html/);
  assert.match(docs, /id="print"/);
  assert.match(docs, /id="insert-table"/);
  assert.match(docs, /id="preview-toggle"/);
  assert.match(docs, /id="preview"/);
  assert.doesNotMatch(docs, /AI assistant|auto-?write|generate copy|magic wand/i);

  assert.match(sheets, /aria-current="page">Sheets/);
  assert.match(sheets, /localStorage/);
  assert.match(sheets, /download-csv/);
  assert.match(sheets, /id="paste-from"/);
  assert.match(sheets, /COLS = 8/);
  assert.match(sheets, /ROWS = 12/);
  assert.doesNotMatch(sheets, /SUM\(|AI assistant|=A1\+/i);

  assert.match(slides, /aria-current="page">Slides/);
  assert.match(slides, /id="slide-title"/);
  assert.match(slides, /id="slide-notes"/);
  assert.match(slides, /data-theme="cream"/);
  assert.match(slides, /No animation/);
  assert.doesNotMatch(slides, /Ken Burns|transition: *all|animation-engine/i);

  assert.match(paste, /tidyRows|function tidy/);
  assert.match(paste, /download-csv/);
  assert.match(paste, /id="messy"/);
  assert.match(paste, /id="preview"/);
  assert.match(paste, /Catalogue tool/);
  assert.match(invoice, /id="invoice-paper"/);
  assert.match(invoice, /download-html/);
  assert.match(notes, /function extractActions/);
  assert.match(notes, /download-md/);
  assert.match(buddy, /catalogue/i);
  assert.match(buddy, /paste-csv\.html/);
  assert.match(buddy, /invoice\.html/);
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
