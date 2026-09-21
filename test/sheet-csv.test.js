import { test } from "node:test";
import assert from "node:assert/strict";
import { applyPaste, colLabel, emptyGrid, parseDelimited, toCsv } from "../src/sheet-csv.js";

test("parseDelimited reads CSV, TSV, and quoted commas", () => {
  assert.deepEqual(parseDelimited("a,b\nc,d"), [
    ["a", "b"],
    ["c", "d"],
  ]);
  assert.deepEqual(parseDelimited("name\tage\nNova\t2"), [
    ["name", "age"],
    ["Nova", "2"],
  ]);
  assert.deepEqual(parseDelimited('"print, download",yes'), [["print, download", "yes"]]);
  assert.deepEqual(parseDelimited('say,"she said ""hi"""'), [["say", 'she said "hi"']]);
  assert.equal(colLabel(0), "A");
  assert.equal(colLabel(7), "H");
});

test("applyPaste fills from the selected cell and stays in bounds", () => {
  const grid = emptyGrid(4, 4);
  const next = applyPaste(grid, "x\ty\n1\t2", 1, 1);
  assert.equal(next[1][1], "x");
  assert.equal(next[1][2], "y");
  assert.equal(next[2][1], "1");
  assert.equal(next[2][2], "2");
  assert.equal(next[0][0], "");
  const clipped = applyPaste(emptyGrid(2, 2), "a,b,c\nd,e,f\ng,h,i", 1, 1);
  assert.equal(clipped[1][1], "a");
  assert.equal(clipped[1][0], "");
});

test("toCsv quotes cells that need it", () => {
  const csv = toCsv([
    ["item", "note"],
    ["print", "hides, chrome"],
  ]);
  assert.match(csv, /"hides, chrome"/);
  assert.match(csv, /^item,note/m);
});
