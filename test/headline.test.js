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
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/sheets.html" } }, names),
    /Nova Chen wrote Meridian Sheets/,
  );
  assert.match(
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/paste-csv.html" } }, names),
    /Nova Chen wrote Paste → CSV/,
  );
  assert.match(
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/invoice.html" } }, names),
    /Nova Chen wrote Invoice/,
  );
  assert.match(
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/meeting-notes.html" } }, names),
    /Nova Chen wrote Notes/,
  );
  assert.match(
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/kanban.html" } }, names),
    /Nova Chen wrote Kanban/,
  );
  assert.match(
    headlineFor({ type: "file_written", actor: "nova", data: { path: "product/units.html" } }, names),
    /Nova Chen wrote Units/,
  );
  assert.match(
    headlineFor({ type: "say", actor: "mira", data: { text: "Kill the settings page." } }, names),
    /Mira Sol said/,
  );
  assert.match(
    headlineFor({ type: "budget_paused", actor: "system", data: {} }, names),
    /token ceiling — world paused/,
  );
  assert.equal(
    headlineFor({ type: "office_edited", actor: "jules", data: { kind: "plant", action: "move" } }, { jules: "Jules Park" }),
    "Jules Park moved the plant.",
  );
  assert.match(
    headlineFor({ type: "office_edited", actor: "jules", data: { action: "whiteboard", kind: "whiteboard" } }, { jules: "Jules Park" }),
    /rewrote the whiteboard/,
  );
  assert.equal(
    headlineFor({ type: "idle_flavor", actor: "system", data: { text: "The lamp stays on." } }, names),
    "The lamp stays on.",
  );
  assert.equal(
    headlineFor({ type: "docs_assist", actor: "system", message: "Docs: a short heading, then the list." }, names),
    "Docs: a short heading, then the list.",
  );
  assert.equal(
    headlineFor({ type: "mesh_error", actor: "system", data: { text: "pi3 offline" } }, names),
    "pi3 offline",
  );
});
