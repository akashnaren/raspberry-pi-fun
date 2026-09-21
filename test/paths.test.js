import { test } from "node:test";
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { isMachineryRel, resolveWorkspacePath } from "../src/paths.js";

const root = resolve("/tmp/workspace-root");

test("blocks machinery and traversal", () => {
  assert.equal(isMachineryRel("src/index.js"), true);
  assert.equal(isMachineryRel("public/office.js"), true);
  assert.equal(isMachineryRel("studio.config.json"), true);
  assert.equal(isMachineryRel(".env"), true);
  assert.equal(isMachineryRel("product/index.html"), false);

  assert.throws(() => resolveWorkspacePath(root, "../src/index.js"), /escapes/);
  assert.throws(() => resolveWorkspacePath(root, "/etc/passwd"), /absolute/);
  assert.throws(() => resolveWorkspacePath(root, "src/orchestrator.js"), /machinery/);
  const ok = resolveWorkspacePath(root, "product/index.html");
  assert.equal(ok.rel, "product/index.html");
});
