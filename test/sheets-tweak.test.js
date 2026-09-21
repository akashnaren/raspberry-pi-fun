import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyNovaSheetsTweak, NOVA_SHEETS_MARK } from "../src/sheets-tweak.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("Nova Sheets tweak is already in the seed and stays idempotent", async () => {
  const seed = await readFile(join(REPO, "workspace/product/sheets.html"), "utf8");
  assert.match(seed, new RegExp(NOVA_SHEETS_MARK));
  assert.match(seed, /Paste fills from the selected cell/);
  assert.match(seed, /function applyPaste/);
  assert.equal(applyNovaSheetsTweak(seed), seed);
  assert.equal(syntaxCheckHtml(applyNovaSheetsTweak(seed)).ok, true);
});

test("Nova Sheets tweak upgrades an older Sheets page", () => {
  const older = `<!doctype html><html><body>
    <header><span>Meridian Office</span></header>
    <footer><span id="kept">Local only.</span></footer>
  </body></html>`;
  const next = applyNovaSheetsTweak(older);
  assert.match(next, /id="paste-from"/);
  assert.match(next, /Paste fills from the selected cell/);
  assert.equal(applyNovaSheetsTweak(next), next);
});
