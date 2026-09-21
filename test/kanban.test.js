import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import vm from "node:vm";
import { addCard, boardJson, boardMarkdown, emptyBoard, moveCard, renameCard } from "../src/kanban.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

function pageApi(html, names) {
  const src = [...String(html).matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]).join("\n");
  const context = { api: null };
  vm.runInNewContext(`${src}\nthis.api = { ${names.join(", ")} };`, context);
  return context.api;
}

test("moveCard shifts a card across Backlog, Doing, and Done", () => {
  let board = emptyBoard();
  board = addCard(board, "backlog", "Write the Friday note");
  board = addCard(board, "backlog", "Label the degrees");
  board = addCard(board, "doing", "Cream chrome");
  assert.equal(board.columns.backlog.length, 2);
  const first = board.columns.backlog[0].id;
  board = moveCard(board, first, "done");
  assert.equal(board.columns.done.length, 1);
  assert.equal(board.columns.done[0].title, "Write the Friday note");
  assert.equal(board.columns.backlog.length, 1);
  assert.equal(board.columns.backlog[0].title, "Label the degrees");
  board = moveCard(board, board.columns.backlog[0].id, "doing", 0);
  assert.equal(board.columns.doing[0].title, "Label the degrees");
  assert.equal(board.columns.doing[1].title, "Cream chrome");
  board = renameCard(board, board.columns.doing[0].id, "Label °F");
  assert.equal(board.columns.doing[0].title, "Label °F");
  assert.equal(moveCard(board, "missing", "done"), board);
  const md = boardMarkdown(board);
  assert.match(md, /## Backlog/);
  assert.match(md, /## Doing/);
  assert.match(md, /## Done/);
  assert.match(md, /- Write the Friday note/);
  assert.match(md, /- Label °F/);
  const json = JSON.parse(boardJson(board));
  assert.equal(json.columns.done[0].title, "Write the Friday note");
  assert.equal(json.columns.doing[0].title, "Label °F");
});

test("kanban.html is a local cream catalogue board", async () => {
  const html = await readFile(join(REPO, "workspace/product/kanban.html"), "utf8");
  assert.equal(syntaxCheckHtml(html).ok, true);
  assert.match(html, /Meridian Office/);
  assert.match(html, /class="rail"/);
  assert.match(html, /aria-current="page">Kanban/);
  assert.match(html, /data-column="backlog"/);
  assert.match(html, /data-column="doing"/);
  assert.match(html, /data-column="done"/);
  assert.match(html, /function moveCard/);
  assert.match(html, /function renameCard/);
  assert.match(html, /function addCard/);
  assert.match(html, /localStorage/);
  assert.match(html, /draggable/);
  assert.match(html, /id="download-md"/);
  assert.match(html, /id="download-json"/);
  const apps = html.match(/class="apps"[\s\S]*?<\/nav>/)[0];
  assert.doesNotMatch(apps, /Kanban|Units|Invoice/i);
  assert.doesNotMatch(html, /sign[- ]?up|checkout|stripe|chat box|AI write/i);
  const api = pageApi(html, ["emptyBoard", "addCard", "moveCard", "renameCard", "boardMarkdown"]);
  let board = api.emptyBoard();
  board = api.addCard(board, "backlog", "One");
  board = api.addCard(board, "backlog", "Two");
  board = api.addCard(board, "doing", "Three");
  board = api.moveCard(board, board.columns.backlog[0].id, "done");
  assert.equal(board.columns.done[0].title, "One");
  assert.equal(board.columns.backlog.length, 1);
  board = api.renameCard(board, board.columns.doing[0].id, "Three renamed");
  assert.match(api.boardMarkdown(board), /Three renamed/);
});
