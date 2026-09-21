import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyNovaDocsTweak, NOVA_DOCS_MARK } from "../src/docs-tweak.js";
import { syntaxCheckHtml } from "../src/validator.js";
import { REPO } from "./helpers.js";

test("Nova Docs tweak is already in the seed and stays idempotent", async () => {
  const seed = await readFile(join(REPO, "workspace/product/index.html"), "utf8");
  assert.match(seed, new RegExp(NOVA_DOCS_MARK));
  assert.match(seed, /function downloadMarkdown/);
  assert.match(seed, /Ctrl\+S writes a \.md/);
  assert.equal(applyNovaDocsTweak(seed), seed);
  assert.equal(syntaxCheckHtml(applyNovaDocsTweak(seed)).ok, true);
});

test("Nova Docs tweak upgrades an older Docs page", () => {
  const older = `<!doctype html><html><body>
    <div id="body">Write here. This tab keeps the page on this machine. Download when you want a file.</div>
    <footer><span id="kept">Local only.</span></footer>
    <script>
      document.getElementById("download-md").addEventListener("click", function () {
        const md = "# " + title.value.trim() + "\\n\\n" + htmlToMarkdown(body);
        download(fileBase() + ".md", "text/markdown;charset=utf-8", md);
      });
      if (key === "b" || key === "i") {
      }
    </script>
  </body></html>`;
  const next = applyNovaDocsTweak(older);
  assert.match(next, /id="save-md"/);
  assert.match(next, /function downloadMarkdown/);
  assert.match(next, /key === "s"/);
  assert.match(next, /Ctrl\+S writes a \.md/);
  assert.equal(applyNovaDocsTweak(next), next);
});
