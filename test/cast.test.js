import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { opinionsFor, emptyMatrix } from "../src/relationships.js";
import { REPO } from "./helpers.js";

test("Meridian Desk cast lock: Nova, Kessler, Mira + Timezone Buddy", async () => {
  const config = JSON.parse(await readFile(join(REPO, "studio.config.json"), "utf8"));
  assert.equal(config.studio.name, "Meridian Desk");
  assert.equal(config.studio.product, "Timezone Buddy");
  const byId = Object.fromEntries(config.employees.map((person) => [person.id, person]));
  assert.equal(byId.nova.name, "Nova Chen");
  assert.equal(byId.nova.role, "programmer");
  assert.equal(byId.nova.accent || byId.nova.color, "#F97316");
  assert.equal(byId.nova.modelTier, "frontier");
  assert.match(byId.nova.priorities, /tonight|Working beats pretty/i);
  assert.equal(byId.kessler.name, "Kessler Holt");
  assert.equal(byId.kessler.role, "qa");
  assert.equal(byId.kessler.accent || byId.kessler.color, "#14B8A6");
  assert.equal(byId.kessler.modelTier, "mid");
  assert.match(byId.kessler.priorities, /loose greens/i);
  assert.equal(byId.mira.name, "Mira Sol");
  assert.equal(byId.mira.role, "producer");
  assert.equal(byId.mira.accent || byId.mira.color, "#F59E0B");
  assert.equal(byId.mira.modelTier, "cheap-capable");
  assert.match(byId.mira.priorities, /scope creep/i);

  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  for (const id of ["nova", "kessler", "mira"]) {
    const desk = office.desks.find((item) => item.owner === id);
    assert.ok(desk, `${id} desk`);
    assert.ok(desk.items.includes("monitor"));
    assert.ok(desk.items.includes("coffee_mug"));
    assert.ok(desk.items.includes("plant"));
  }
  assert.ok(office.decor.some((item) => item.kind === "whiteboard" && /Timezone Buddy/.test(item.text)));
  assert.ok(office.rooms.some((room) => /break/i.test(room.name)));
  assert.ok(office.decor.some((item) => item.kind === "coffee"));

  const product = await readFile(join(REPO, "workspace/product/index.html"), "utf8");
  assert.match(product, /Timezone Buddy/);
  assert.doesNotMatch(product, /untitled|Stamp/i);

  const matrix = emptyMatrix();
  assert.equal(opinionsFor(matrix, "nova").find((item) => item.other === "kessler").score, -1);
  assert.equal(opinionsFor(matrix, "mira").find((item) => item.other === "nova").score, 1);
  assert.equal(opinionsFor(matrix, "mira").find((item) => item.other === "kessler").score, 0);
});
