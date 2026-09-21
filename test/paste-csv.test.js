import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { detectDelimiter, tidyRows, toCsv } from "../src/paste-csv.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("tidyRows reads CSV, TSV, and space-ish columns", () => {
  assert.deepEqual(tidyRows("name,city\nNova,desk"), [
    ["name", "city"],
    ["Nova", "desk"],
  ]);
  assert.deepEqual(tidyRows("name\tcity\nKessler\ttable"), [
    ["name", "city"],
    ["Kessler", "table"],
  ]);
  assert.deepEqual(tidyRows("name    city    note\nNova    desk    ships"), [
    ["name", "city", "note"],
    ["Nova", "desk", "ships"],
  ]);
  assert.deepEqual(tidyRows("a  b\nc  d"), [
    ["a", "b"],
    ["c", "d"],
  ]);
  assert.deepEqual(tidyRows('"print, download",yes'), [["print, download", "yes"]]);
  assert.equal(detectDelimiter("a\tb"), "\t");
  assert.match(toCsv([["item", "note"], ["print", "hides, chrome"]]), /"hides, chrome"/);
});

test("paste-csv.html is a local cream catalogue tool", async () => {
  const html = await readFile(join(REPO, "workspace/product/paste-csv.html"), "utf8");
  assert.equal(syntaxCheckHtml(html).ok, true);
  assert.match(html, /Meridian Office/);
  assert.match(html, /class="rail"/);
  assert.match(html, /class="apps"/);
  assert.match(html, /aria-current="page">Paste → CSV/);
  const apps = html.match(/class="apps"[\s\S]*?<\/nav>/)[0];
  assert.doesNotMatch(apps, /paste-csv|Timezone/i);
  assert.doesNotMatch(html, /sign[- ]?up|checkout|stripe|chat box/i);
});
