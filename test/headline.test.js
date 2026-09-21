import { test } from "node:test";
import assert from "node:assert/strict";
import { headlineFor } from "../src/headline.js";

const names = {
  mira: "Mira Sol",
  nova: "Nova Chen",
  kessler: "Kessler Holt",
  system: "Meridian Desk",
};

test("headlines are plain English a stranger can read", () => {
  assert.equal(
    headlineFor({ type: "build_failed", actor: "kessler", data: {} }, names),
    "Kessler rejected Nova's write.",
  );
  assert.match(
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/index.html" } }, names),
    /Nova Chen wrote Meridian Office/,
  );
  assert.match(
    headlineFor({ type: "say", actor: "mira", data: { text: "Kill the settings page." } }, names),
    /Mira Sol said/,
  );
  assert.match(
    headlineFor({ type: "budget_paused", actor: "system", data: {} }, names),
    /token ceiling — world paused/,
  );
});
