import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadEnvironmentFiles, parseEnvironmentFile } from "../src/env-file.js";

test("EnvironmentFile parser skips comments and accepts quotes", () => {
  const parsed = parseEnvironmentFile(`
# OpenRouter
OPENROUTER_API_KEY=
DRY_RUN=true
QUOTED="hello world"
SINGLE='x'
INVALID
=nope
`);
  assert.equal(parsed.OPENROUTER_API_KEY, "");
  assert.equal(parsed.DRY_RUN, "true");
  assert.equal(parsed.QUOTED, "hello world");
  assert.equal(parsed.SINGLE, "x");
  assert.equal(parsed.INVALID, undefined);
});

test("missing EnvironmentFile paths still start; secrets override empty .env", async () => {
  const root = await mkdtemp(join(tmpdir(), "fishbowl-env-"));
  const home = await mkdtemp(join(tmpdir(), "fishbowl-home-"));
  await writeFile(join(root, ".env"), "DRY_RUN=true\nOPENROUTER_API_KEY=\n");
  await mkdir(join(home, ".secrets", "fishbowl"), { recursive: true });
  await writeFile(
    join(home, ".secrets", "fishbowl", "openrouter.env"),
    "OPENROUTER_API_KEY=sk-or-test\nDRY_RUN=false\n",
  );

  const env = {};
  const loaded = loadEnvironmentFiles({ root, home, env });
  assert.equal(loaded.length, 2);
  assert.equal(env.OPENROUTER_API_KEY, "sk-or-test");
  assert.equal(env.DRY_RUN, "false");
});

test("already-set env wins; missing secret file does not require a key", async () => {
  const root = await mkdtemp(join(tmpdir(), "fishbowl-env-"));
  const home = await mkdtemp(join(tmpdir(), "fishbowl-home-"));
  const env = { DRY_RUN: "true", OPENROUTER_API_KEY: "" };
  const loaded = loadEnvironmentFiles({ root, home, env });
  assert.deepEqual(loaded, []);
  assert.equal(env.DRY_RUN, "true");
  assert.equal(env.OPENROUTER_API_KEY, "");
});

test("DRY_RUN defaults true when unset", async () => {
  const root = await mkdtemp(join(tmpdir(), "fishbowl-env-"));
  const home = await mkdtemp(join(tmpdir(), "fishbowl-home-"));
  const env = {};
  loadEnvironmentFiles({ root, home, env });
  assert.equal(env.DRY_RUN, "true");
});
