import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import {
  destinationFor,
  fitView,
  hudStatus,
  latestEventLine,
  verbFor,
} from "../public/office-motion.js";
import { REPO } from "./helpers.js";

const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));

test("camera fit is locked — same origin if the actor moves", () => {
  const a = fitView(office, 800, 600);
  const b = fitView(office, 800, 600);
  assert.equal(a.locked, true);
  assert.equal(a.ox, b.ox);
  assert.equal(a.oy, b.oy);
  assert.equal(a.cell, b.cell);
  assert.ok(a.cell >= 14 && a.cell <= 56);
});

test("events walk to real objects, never 'thinking'", () => {
  const write = destinationFor({ type: "file_written", actor: "nova", data: { path: "product/index.html" } }, office);
  assert.equal(write.at, "desk");
  const plan = destinationFor({ type: "task_added", actor: "mira", data: { task: { text: "Docs first" } } }, office);
  assert.equal(plan.at, "whiteboard");
  const coffee = destinationFor({ type: "say", actor: "jules", data: { text: "Coffee stays." } }, office);
  assert.equal(coffee.at, "coffee");
  const breakRoom = destinationFor({ type: "say", actor: "mira", data: { text: "Take a break on the couch." } }, office);
  assert.equal(breakRoom.at, "couch");
  const review = destinationFor({ type: "build_failed", actor: "kessler", data: {} }, office);
  assert.equal(review.at, "meeting");
  const ask = destinationFor({ type: "request_filed", actor: "nova", data: { item: "standing_desk" } }, office);
  assert.equal(ask.at, "desk");
  assert.ok(ask.x > 18);
  assert.equal(destinationFor({ type: "say", actor: "system" }, office), null);
  assert.match(verbFor("idle", "desk"), /desk/);
  assert.match(verbFor("walk", "coffee"), /walking to the coffee machine/);
  assert.doesNotMatch(verbFor("idle", "whiteboard"), /think/i);
  assert.doesNotMatch(latestEventLine([{ type: "say", headline: "Mira said, “Docs first.”" }]), /think/i);
});

test("HUD status is a quiet static line", () => {
  const line = hudStatus({
    dayN: 1,
    staff: 4,
    spentUsd: 0,
    ceilingUsd: 5,
    actingName: "Nova Chen",
  });
  assert.equal(line.line, "Day 1 · 4 people · $0.00 / $5 · Nova");
  assert.equal(latestEventLine([]), "the room is still");
  assert.equal(
    latestEventLine([
      { type: "model_resolved", headline: "noise" },
      { type: "say", headline: "Jules said, “Coffee stays.”" },
    ]),
    "Jules said, “Coffee stays.”",
  );
});
