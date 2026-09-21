import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  applyRelationshipEvent,
  createRelationships,
  decayTowardZero,
  emptyMatrix,
  opinionsFor,
} from "../src/relationships.js";
import { assembleContext } from "../src/context.js";
import { createEventLog } from "../src/event-log.js";
import { tempStudioRoot } from "./helpers.js";

test("seed matrix and slow decay toward zero", () => {
  const matrix = emptyMatrix();
  const nova = opinionsFor(matrix, "nova");
  const kessler = nova.find((item) => item.other === "kessler");
  assert.equal(kessler.score, -1);
  const miraNova = opinionsFor(matrix, "mira").find((item) => item.other === "nova");
  assert.equal(miraNova.score, 1);
  const decayed = decayTowardZero(matrix, 1);
  const after = opinionsFor(decayed, "nova").find((item) => item.other === "kessler");
  assert.ok(after.score > -1 && after.score < 0);
});

test("reject events nudge Kessler vs Nova", () => {
  const next = applyRelationshipEvent(emptyMatrix(), { type: "build_failed", actor: "kessler" });
  const score = opinionsFor(next, "kessler").find((item) => item.other === "nova").score;
  assert.ok(score < -1);
});

test("context assembly includes last opinions", async () => {
  const root = await tempStudioRoot();
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const { messages } = await assembleContext({
    workspaceRoot: join(root, "workspace"),
    employee: {
      id: "nova",
      name: "Nova Chen",
      role: "programmer",
      priorities: "ship tonight",
      model: "openai/gpt-4o-mini",
      modelFamily: "openai",
    },
    employees: [
      { id: "nova", name: "Nova Chen", role: "programmer", priorities: "ship", modelFamily: "openai" },
      { id: "kessler", name: "Kessler Holt", role: "qa", priorities: "reject loose greens", modelFamily: "nousresearch" },
      { id: "mira", name: "Mira Sol", role: "producer", priorities: "kill scope", modelFamily: "x-ai" },
    ],
    events,
    constitution: "c",
    strategy: "s",
  });
  assert.match(messages[0].content, /Meridian Desk/);
  assert.match(messages[0].content, /Timezone Buddy/);
  assert.match(messages[1].content, /Kessler Holt: -1/);
  assert.match(messages[0].content, /reasonable professionals/);
  assert.equal(messages[0].content.includes("you are competitive"), false);
});

test("relationships persist a decay day without breaking dry-run", async () => {
  const dir = await mkdtemp(join(tmpdir(), "rels-"));
  const filePath = join(dir, "relationships.json");
  const rels = await createRelationships({ filePath, now: () => Date.parse("2026-09-21T10:00:00Z") });
  await rels.decayIfNewDay();
  const raw = JSON.parse(await readFile(filePath, "utf8"));
  assert.equal(raw.decayDay, "2026-09-21");
  assert.equal(rels.opinionsFor("mira").find((item) => item.other === "kessler").score, 0);
});
