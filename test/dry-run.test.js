import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyNovaSheetsTweak } from "../src/sheets-tweak.js";
import { createDryRunDriver, dryRunCalls } from "../src/dry-run.js";
import { createEventLog } from "../src/event-log.js";
import { createBudget } from "../src/budget.js";
import { createKillSwitch } from "../src/kill-switch.js";
import { createOrchestrator } from "../src/orchestrator.js";
import { destinationFor } from "../public/office-motion.js";
import { createStudio } from "../src/studio.js";
import { createToolRunner } from "../src/tools.js";
import { tempStudioRoot, testEnv } from "./helpers.js";

const office = JSON.parse(await readFile(new URL("../workspace/office.json", import.meta.url), "utf8"));

test("dry-run day: Nova ships Sheets, Kessler files a paste bug, Mira sets backlog, Jules tidies", async () => {
  const seed = await readFile(new URL("../workspace/product/sheets.html", import.meta.url), "utf8");
  const nova0 = dryRunCalls("nova", 0, { sheetsHtml: seed });
  assert.ok(nova0.some((call) => call.name === "read_file" && call.arguments.path === "product/sheets.html"));
  assert.ok(nova0.some((call) => call.name === "write_file" && call.arguments.path === "product/sheets.html"));
  const shipped = nova0.find((call) => call.name === "write_file");
  assert.equal(shipped.arguments.contents, applyNovaSheetsTweak(seed));
  assert.match(shipped.arguments.contents, /Paste fills from the selected cell/);
  assert.ok(nova0.some((call) => call.name === "say" && call.arguments.to === "mira"));

  const kessler0 = dryRunCalls("kessler", 0);
  assert.ok(kessler0.some((call) => call.name === "add_task" && /Bug: paste/.test(call.arguments.text)));
  assert.ok(kessler0.some((call) => call.name === "journal" && /Bug note/.test(call.arguments.text)));
  assert.ok(kessler0.some((call) => call.name === "say" && call.arguments.to === "mira"));

  const mira1 = dryRunCalls("mira", 1);
  assert.ok(mira1.some((call) => call.name === "add_task" && /Invoice print page/.test(call.arguments.text)));
  assert.ok(mira1.some((call) => call.name === "journal" && /Backlog/.test(call.arguments.text)));

  const jules1 = dryRunCalls("jules", 1);
  const tidy = jules1.find((call) => call.name === "edit_office");
  assert.equal(tidy.arguments.deskItem.owner, "mira");
  assert.equal(tidy.arguments.deskItem.item, "sticky_notes");
  assert.match(tidy.arguments.whiteboard, /Sheets/);
});

test("dry-run scripts walk to coffee, board, couch, and table", () => {
  const mira0 = dryRunCalls("mira", 0).find((call) => call.name === "say");
  const mira1 = dryRunCalls("mira", 1).find((call) => call.name === "say");
  const mira2 = dryRunCalls("mira", 2).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "mira", data: { text: mira0.arguments.message } }, office).at, "whiteboard");
  assert.equal(destinationFor({ type: "say", actor: "mira", data: { text: mira1.arguments.message } }, office).at, "coffee");
  assert.equal(destinationFor({ type: "say", actor: "mira", data: { text: mira2.arguments.message } }, office).at, "couch");

  const kessler0 = dryRunCalls("kessler", 0).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "kessler", data: { text: kessler0.arguments.message } }, office).at, "meeting");

  const nova2 = dryRunCalls("nova", 2).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "nova", data: { text: nova2.arguments.message } }, office).at, "coffee");
});

test("Jules dry-run uses edit_office and it persists in office.json", async () => {
  const root = await tempStudioRoot();
  const workspaceRoot = join(root, "workspace");
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const tools = createToolRunner({
    workspaceRoot,
    events,
    employees: [{ id: "jules", name: "Jules Park", role: "office_manager" }],
  });

  const calls = dryRunCalls("jules", 0);
  assert.ok(calls.some((call) => call.name === "edit_office"));
  assert.ok(calls.some((call) => call.name === "say"));
  for (const call of calls) {
    const result = await tools.execute("jules", call.name, call.arguments);
    assert.equal(result.ok, true, result.error);
  }

  const after = JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8"));
  assert.ok(after.decor.some((item) => item.kind === "plant" && item.x === 19 && item.y === 10));
  assert.equal(after.budget.furniture, 2);
  assert.equal(events.all().some((event) => event.type === "office_edited" && event.actor === "jules"), true);

  const boardTurn = dryRunCalls("jules", 1);
  for (const call of boardTurn) {
    const result = await tools.execute("jules", call.name, call.arguments);
    assert.equal(result.ok, true, result.error);
  }
  const signed = JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8"));
  assert.match(signed.decor.find((item) => item.kind === "whiteboard").text, /Paste/);
  assert.ok(signed.desks.find((desk) => desk.owner === "mira").items.includes("sticky_notes"));
});

test("Nova dry-run ship writes Sheets and promotes the green seed to dist", async () => {
  const root = await tempStudioRoot();
  const studio = await createStudio({
    root,
    env: testEnv(),
    listen: false,
    random: () => 0,
  });
  const seedDocs = await readFile(join(root, "dist/index.html"), "utf8");
  const seedSheets = await readFile(join(root, "dist/sheets.html"), "utf8");
  const seedSlides = await readFile(join(root, "dist/slides.html"), "utf8");
  assert.match(seedDocs, /id="save-md"/);
  assert.match(seedSheets, /id="paste-from"/);
  assert.match(seedSlides, /id="slide-title"/);
  const result = await studio.orchestrator.tickOnce();
  assert.equal(result.employee.id, "nova");
  assert.equal(result.result.dryRun, true);
  assert.ok(result.result.toolCalls.some((call) => call.name === "write_file" && call.arguments.path === "product/sheets.html"));
  assert.equal(
    studio.events.all().some((event) => event.type === "file_written" && event.data?.path === "product/sheets.html"),
    true,
  );
  assert.equal(studio.events.all().some((event) => event.type === "build_passed" && event.actor === "nova"), true);
  const after = await readFile(join(root, "dist/sheets.html"), "utf8");
  assert.match(after, /Paste fills from the selected cell/);
  const docsAfter = await readFile(join(root, "dist/index.html"), "utf8");
  assert.match(docsAfter, /id="save-md"/);
  await studio.stop();
});

test("dry-run driver stays $0 and Jules still edits on later turns", () => {
  const driver = createDryRunDriver();
  const first = driver.complete({ employee: { id: "jules", role: "office_manager" } });
  const later = driver.complete({ employee: { id: "jules", role: "office_manager" } });
  assert.equal(first.dryRun, true);
  assert.equal(first.costUsd, 0);
  assert.ok(first.toolCalls.some((call) => call.name === "edit_office"));
  assert.ok(later.toolCalls.some((call) => call.name === "edit_office"));
});

test("orchestrator Jules tick writes office.json in dry-run", async () => {
  const root = await tempStudioRoot();
  const workspaceRoot = join(root, "workspace");
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const budget = await createBudget({ filePath: join(root, "data", "spend.json"), dailyCeilingUsd: 5 });
  const killSwitch = await createKillSwitch({ filePath: join(root, "data", "PAUSED") });
  const tools = createToolRunner({
    workspaceRoot,
    events,
    employees: [{ id: "jules", name: "Jules Park", role: "office_manager" }],
  });
  const driver = createDryRunDriver();
  const orchestrator = createOrchestrator({
    workspaceRoot,
    employees: [{ id: "jules", name: "Jules Park", role: "office_manager", model: "dry-run" }],
    lottery: { jules: 1 },
    tick: { minMs: 90_000, maxMs: 120_000 },
    events,
    tools,
    llm: {
      dryRun: true,
      complete: (args) => driver.complete(args),
    },
    budget,
    killSwitch,
    random: () => 0,
  });

  const before = JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8"));
  const result = await orchestrator.tickOnce();
  assert.equal(result.employee.id, "jules");
  assert.equal(result.result.dryRun, true);
  const after = JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8"));
  assert.notDeepEqual(after.decor, before.decor);
  assert.equal(after.budget.furniture, before.budget.furniture);
});
