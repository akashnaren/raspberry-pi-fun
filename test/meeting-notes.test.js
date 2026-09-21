import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { actionsMarkdown, extractActions, ownerIn } from "../src/meeting-notes.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("extractActions pulls owners from human notes", () => {
  const actions = extractActions(`Mira: cut payments.
Nova will ship the invoice.
Action: Kessler check print margins.
Jules to rewrite the board.
We talked about coffee.`);
  assert.equal(ownerIn("Nova will ship").name, "Nova");
  assert.equal(actions.length, 4);
  assert.equal(actions.find((item) => item.ownerId === "mira").text, "cut payments");
  assert.equal(actions.find((item) => item.ownerId === "nova").text, "ship the invoice");
  assert.equal(actions.find((item) => item.ownerId === "kessler").text, "Kessler check print margins");
  assert.equal(actions.find((item) => item.ownerId === "jules").text, "rewrite the board");
  const md = actionsMarkdown(actions);
  assert.match(md, /# Action items/);
  assert.match(md, /- \[ \] cut payments \(Mira\)/);
  assert.match(md, /rewrite the board \(Jules\)/);
});

test("meeting-notes.html is a local cream catalogue tool", async () => {
  const html = await readFile(join(REPO, "workspace/product/meeting-notes.html"), "utf8");
  assert.equal(syntaxCheckHtml(html).ok, true);
  assert.match(html, /Meridian Office/);
  assert.match(html, /class="rail"/);
  assert.match(html, /aria-current="page">Notes/);
  assert.match(html, /function extractActions/);
  assert.match(html, /function actionsMarkdown/);
  assert.match(html, /localStorage/);
  assert.match(html, /download-md/);
  const apps = html.match(/class="apps"[\s\S]*?<\/nav>/)[0];
  assert.doesNotMatch(apps, /Notes|Timezone|invoice/i);
  assert.doesNotMatch(html, /sign[- ]?up|checkout|stripe|chat box/i);
});
