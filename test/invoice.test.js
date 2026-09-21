import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { invoiceReadyHtml, parseInvoice, parseItemLine } from "../src/invoice.js";
import { applyNovaInvoiceTweak, NOVA_INVOICE_MARK } from "../src/invoice-tweak.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("parseInvoice reads paste lines into a printable total", () => {
  const doc = parseInvoice(`estimate
from: North Shop
to: Harbor Press
EST-12
Design hours    8    90
Print proof, 1, 25`);
  assert.equal(doc.kind, "estimate");
  assert.equal(doc.from, "North Shop");
  assert.equal(doc.to, "Harbor Press");
  assert.equal(doc.number, "EST-12");
  assert.equal(doc.items.length, 2);
  assert.equal(doc.items[0].desc, "Design hours");
  assert.equal(doc.items[0].qty, 8);
  assert.equal(doc.total, 745);
  assert.deepEqual(parseItemLine("Cable x2 @12"), { desc: "Cable", qty: 2, price: 12 });
  const html = invoiceReadyHtml(doc);
  assert.match(html, /<h1>Estimate<\/h1>/);
  assert.match(html, /Harbor Press/);
  assert.match(html, /745\.00/);
  assert.match(html, /@page/);
  assert.doesNotMatch(html, /stripe|checkout|sign[- ]?up/i);
});

test("invoice.html is a local cream catalogue tool", async () => {
  const html = await readFile(join(REPO, "workspace/product/invoice.html"), "utf8");
  assert.equal(syntaxCheckHtml(html).ok, true);
  assert.match(html, /Meridian Office/);
  assert.match(html, /class="rail"/);
  assert.match(html, /aria-current="page">Invoice/);
  assert.match(html, /id="invoice-paper"/);
  assert.match(html, /function parseInvoice/);
  assert.match(html, /function invoiceReadyHtml/);
  assert.match(html, /print-margin/);
  assert.match(html, /@page/);
  assert.match(html, /localStorage/);
  const apps = html.match(/class="apps"[\s\S]*?<\/nav>/)[0];
  assert.doesNotMatch(apps, /invoice|Timezone|Notes/i);
  assert.doesNotMatch(html, /sign[- ]?up|checkout|stripe|chat box/i);
  assert.equal(applyNovaInvoiceTweak(html), html);
  assert.match(html, new RegExp(NOVA_INVOICE_MARK));
});
