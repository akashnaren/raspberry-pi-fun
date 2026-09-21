import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyNovaUnitsTweak } from "../src/units-tweak.js";
import { createDryRunDriver, dryRunCalls } from "../src/dry-run.js";
import { settlePose } from "../public/office-motion.js";
import { createEventLog } from "../src/event-log.js";
import { createBudget } from "../src/budget.js";
import { createKillSwitch } from "../src/kill-switch.js";
import { createOrchestrator } from "../src/orchestrator.js";
import { companionId, destinationFor, standBeside } from "../public/office-motion.js";
import { createStudio } from "../src/studio.js";
import { createToolRunner } from "../src/tools.js";
import { tempStudioRoot, testEnv } from "./helpers.js";

const office = JSON.parse(await readFile(new URL("../workspace/office.json", import.meta.url), "utf8"));

test("dry-run evening: Mira runs the kanban, Nova ships units, Kessler rejects deg, Jules writes the board", async () => {
  const seed = await readFile(new URL("../workspace/product/units.html", import.meta.url), "utf8");
  const nova0 = dryRunCalls("nova", 0, { unitsHtml: seed });
  assert.ok(nova0.some((call) => call.name === "read_file" && call.arguments.path === "product/units.html"));
  assert.ok(nova0.some((call) => call.name === "write_file" && call.arguments.path === "product/units.html"));
  const shipped = nova0.find((call) => call.name === "write_file");
  assert.equal(shipped.arguments.contents, applyNovaUnitsTweak(seed));
  assert.match(shipped.arguments.contents, /data-unit-labels="precise"/);
  assert.match(shipped.arguments.contents, /function convert/);
  assert.ok(nova0.some((call) => call.name === "say" && /card/.test(call.arguments.message) && call.arguments.to === "mira"));

  const kessler0 = dryRunCalls("kessler", 0);
  assert.ok(kessler0.some((call) => call.name === "add_task" && /Bug: unit label deg/.test(call.arguments.text)));
  assert.ok(kessler0.some((call) => call.name === "journal" && /°F|fuzzy/.test(call.arguments.text)));
  assert.ok(kessler0.some((call) => call.name === "say" && /table|Reject/.test(call.arguments.message)));

  const mira0 = dryRunCalls("mira", 0);
  assert.ok(mira0.some((call) => call.name === "add_task" && /Kanban|Backlog/.test(call.arguments.text)));
  assert.ok(mira0.some((call) => call.name === "say" && /kanban|columns|board/i.test(call.arguments.message)));

  const mira1 = dryRunCalls("mira", 1);
  assert.ok(mira1.some((call) => call.name === "say" && call.arguments.to === "jules" && /[Cc]offee/.test(call.arguments.message)));
  assert.ok(mira1.some((call) => call.name === "journal" && /kanban|units|accounts/i.test(call.arguments.text)));

  const jules1 = dryRunCalls("jules", 1);
  const tidy = jules1.find((call) => call.name === "edit_office");
  assert.equal(tidy.arguments.deskItem.owner, "mira");
  assert.equal(tidy.arguments.deskItem.item, "sticky_notes");
  assert.equal(tidy.arguments.whiteboard, "SHIP: kanban · units");
});

test("dry-run scripts walk to coffee, board, couch, and table; named say pulls a pair", () => {
  const mira0 = dryRunCalls("mira", 0).find((call) => call.name === "say");
  const mira1 = dryRunCalls("mira", 1).find((call) => call.name === "say");
  const mira2 = dryRunCalls("mira", 2).find((call) => call.name === "say");
  const boardEvent = { type: "say", actor: "mira", data: { text: mira0.arguments.message, to: mira0.arguments.to } };
  assert.equal(destinationFor(boardEvent, office).at, "whiteboard");
  assert.equal(companionId(boardEvent), "nova");
  assert.equal(standBeside(destinationFor(boardEvent, office), 1, 0).at, "whiteboard");
  assert.equal(settlePose("whiteboard", "talk"), "stand-talk");
  const coffeeEvent = { type: "say", actor: "mira", data: { text: mira1.arguments.message, to: mira1.arguments.to } };
  assert.equal(destinationFor(coffeeEvent, office).at, "coffee");
  assert.equal(companionId(coffeeEvent), "jules");
  assert.equal(standBeside(destinationFor(coffeeEvent, office), 1, 0).at, "coffee");
  assert.equal(settlePose("coffee", "talk"), "stand-talk");
  assert.equal(destinationFor({ type: "say", actor: "mira", data: { text: mira2.arguments.message } }, office).at, "couch");
  assert.equal(settlePose("couch", "talk"), "sit-talk");

  const kessler0 = dryRunCalls("kessler", 0).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "kessler", data: { text: kessler0.arguments.message } }, office).at, "meeting");
  assert.equal(companionId({ type: "say", actor: "kessler", data: { text: kessler0.arguments.message, to: kessler0.arguments.to } }), "nova");
  assert.equal(settlePose("meeting", "talk"), "stand-talk");

  const kessler2 = dryRunCalls("kessler", 2).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "kessler", data: { text: kessler2.arguments.message } }, office).at, "coffee");
  assert.equal(companionId({ type: "say", actor: "kessler", data: { text: kessler2.arguments.message, to: "jules" } }), "jules");

  const nova0 = dryRunCalls("nova", 0, { unitsHtml: "<html></html>" }).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "nova", data: { text: nova0.arguments.message } }, office).at, "whiteboard");
  assert.equal(companionId({ type: "say", actor: "nova", data: { text: nova0.arguments.message, to: "mira" } }), "mira");

  const nova1 = dryRunCalls("nova", 1).find((call) => call.name === "say");
  assert.equal(destinationFor({ type: "say", actor: "nova", data: { text: nova1.arguments.message } }, office).at, "desk");
  assert.equal(settlePose("desk", "talk"), "sit-talk");
  assert.equal(settlePose("desk", "type"), "sit-type");
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
  assert.ok(after.decor.some((item) => item.kind === "plant" && item.x === 1 && item.y === 15));
  assert.equal(after.budget.furniture, 2);
  assert.equal(events.all().some((event) => event.type === "office_edited" && event.actor === "jules"), true);

  const boardTurn = dryRunCalls("jules", 1);
  for (const call of boardTurn) {
    const result = await tools.execute("jules", call.name, call.arguments);
    assert.equal(result.ok, true, result.error);
  }
  const signed = JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8"));
  assert.equal(signed.decor.find((item) => item.kind === "whiteboard").text, "SHIP: kanban · units");
  assert.ok(signed.desks.find((desk) => desk.owner === "mira").items.includes("sticky_notes"));
});

test("Nova dry-run ship writes Units and promotes the green seed to dist", async () => {
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
  const seedPaste = await readFile(join(root, "dist/paste-csv.html"), "utf8");
  const seedInvoice = await readFile(join(root, "dist/invoice.html"), "utf8");
  const seedNotes = await readFile(join(root, "dist/meeting-notes.html"), "utf8");
  const seedKanban = await readFile(join(root, "dist/kanban.html"), "utf8");
  const seedUnits = await readFile(join(root, "dist/units.html"), "utf8");
  assert.match(seedDocs, /id="find-box"/);
  assert.match(seedDocs, /kanban\.html/);
  assert.match(seedDocs, /units\.html/);
  assert.match(seedSheets, /id="paste-from"/);
  assert.match(seedSlides, /id="slide-notes"/);
  assert.match(seedPaste, /id="messy"/);
  assert.match(seedInvoice, /id="invoice-paper"/);
  assert.match(seedNotes, /function extractActions/);
  assert.match(seedKanban, /function moveCard/);
  assert.match(seedUnits, /function convert/);
  assert.match(seedUnits, /id="demo-rates"/);
  const result = await studio.orchestrator.tickOnce();
  assert.equal(result.employee.id, "nova");
  assert.equal(result.result.dryRun, true);
  assert.equal(result.result.costUsd, 0);
  assert.ok(result.result.toolCalls.some((call) => call.name === "write_file" && call.arguments.path === "product/units.html"));
  assert.equal(
    studio.events.all().some((event) => event.type === "file_written" && event.data?.path === "product/units.html"),
    true,
  );
  assert.equal(studio.events.all().some((event) => event.type === "build_passed" && event.actor === "nova"), true);
  const after = await readFile(join(root, "dist/units.html"), "utf8");
  assert.match(after, /function convert/);
  assert.match(after, /data-unit-labels="precise"/);
  assert.match(after, /demo rates, not live FX/);
  const afterDocs = await readFile(join(root, "dist/index.html"), "utf8");
  assert.match(afterDocs, /kanban\.html/);
  assert.match(afterDocs, /units\.html/);
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
