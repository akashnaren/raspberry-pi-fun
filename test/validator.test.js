import { test } from "node:test";
import assert from "node:assert/strict";
import { syntaxCheckHtml, syntaxCheckJs } from "../src/validator.js";

test("syntax check rejects broken html and js without executing it", () => {
  assert.equal(syntaxCheckHtml("<!doctype html><html><body>ok</body></html>").ok, true);
  assert.equal(syntaxCheckHtml("<div>nope</div>").ok, false);
  assert.equal(syntaxCheckHtml("<!doctype html><html><script>").ok, false);
  assert.equal(syntaxCheckJs("const x = (1 + 2);").ok, true);
  assert.equal(syntaxCheckJs("const x = (1 + 2;").ok, false);
  assert.equal(syntaxCheckJs("const x = `unterminated").ok, false);
});
