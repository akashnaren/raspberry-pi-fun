import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("Meridian Office Docs is a green seed; Sheets/Slides are stubs; Timezone Buddy stays", async () => {
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
  assert.match(docs, /download/);
  assert.match(sheets, /Sheets later/);
  assert.match(slides, /Slides after/);
  assert.match(buddy, /Timezone Buddy/);
  for (const page of [docs, sheets, slides, buddy]) {
    assert.doesNotMatch(page, /sign[- ]?up|create an account|checkout|stripe|upload a file|chat box|live chat/i);
  }
});

test("board seeds useful tools and keeps DRY_RUN", async () => {
  const backlog = JSON.parse(await readFile(join(REPO, "workspace/backlog.json"), "utf8"));
  const texts = backlog.tasks.map((task) => task.text).join("\n");
  assert.match(texts, /Paste-to-CSV/);
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
