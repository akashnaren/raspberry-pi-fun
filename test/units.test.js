import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import vm from "node:vm";
import { convert, formatAmount } from "../src/units.js";
import { applyNovaUnitsTweak } from "../src/units-tweak.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

function pageApi(html, names) {
  const src = [...String(html).matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]).join("\n");
  const context = { api: null };
  vm.runInNewContext(`${src}\nthis.api = { ${names.join(", ")} };`, context);
  return context.api;
}

test("convert turns 72°F into °C and 5 km into miles", () => {
  const temp = convert(72, "F", "C");
  assert.equal(temp.ok, true);
  assert.ok(Math.abs(temp.result - ((72 - 32) * 5) / 9) < 1e-9);
  assert.equal(temp.labelFrom, "°F");
  assert.equal(temp.labelTo, "°C");
  assert.equal(temp.text, formatAmount(temp.result));
  assert.equal(temp.text, "22.2222");

  const miles = convert(5, "km", "mi");
  assert.equal(miles.ok, true);
  assert.ok(Math.abs(miles.result - (5 * 1000) / 1609.344) < 1e-9);
  assert.equal(miles.labelTo, "mi");
  assert.equal(miles.text, "3.1069");

  const fx = convert(10, "USD", "EUR");
  assert.equal(fx.ok, true);
  assert.ok(Math.abs(fx.result - 9.2) < 1e-9);
  assert.match(fx.note, /demo rates, not live FX/);

  const mixed = convert(1, "km", "kg");
  assert.equal(mixed.ok, false);
});

test("units tweak replaces a fuzzy deg label and stays idempotent", () => {
  const stub =
    '<!doctype html><html><body><select><option value="F">deg F</option><option value="C">deg C</option></select><footer></footer><script>function convert(v){return v}</script></body></html>';
  const next = applyNovaUnitsTweak(stub);
  assert.match(next, /data-unit-labels="precise"/);
  assert.match(next, /id="demo-rates"/);
  assert.match(next, /demo rates, not live FX/);
  assert.match(next, /°F/);
  assert.match(next, /°C/);
  assert.doesNotMatch(next, /deg F|deg C/);
  assert.equal(applyNovaUnitsTweak(next), next);
});

test("units.html is a local cream catalogue converter", async () => {
  const html = await readFile(join(REPO, "workspace/product/units.html"), "utf8");
  assert.equal(syntaxCheckHtml(html).ok, true);
  assert.match(html, /Meridian Office/);
  assert.match(html, /class="rail"/);
  assert.match(html, /aria-current="page">Units/);
  assert.match(html, /id="amount"/);
  assert.match(html, /id="from-unit"/);
  assert.match(html, /id="to-unit"/);
  assert.match(html, /id="result"/);
  assert.match(html, /id="demo-rates"/);
  assert.match(html, /data-unit-labels="precise"/);
  assert.match(html, /function convert/);
  assert.match(html, /1609\.344/);
  assert.match(html, /demo rates, not live FX/);
  assert.match(html, /°F/);
  assert.match(html, /°C/);
  assert.doesNotMatch(html, /deg F|deg C|live FX feed|fetch\(/i);
  assert.doesNotMatch(html, /sign[- ]?up|checkout|stripe|chat box/i);
  assert.equal(applyNovaUnitsTweak(html), html);
  const apps = html.match(/class="apps"[\s\S]*?<\/nav>/)[0];
  assert.doesNotMatch(apps, /Units|Kanban|Invoice/i);
  const api = pageApi(html, ["convert"]);
  const temp = api.convert(72, "F", "C");
  assert.equal(temp.text, "22.2222");
  assert.equal(temp.labelTo, "°C");
  const miles = api.convert(5, "km", "mi");
  assert.equal(miles.text, "3.1069");
  assert.equal(miles.labelTo, "mi");
});
