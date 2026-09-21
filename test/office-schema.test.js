import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { validateOffice } from "../src/schemas.js";
import { REPO } from "./helpers.js";

test("seed office is a dollhouse the renderer can draw", async () => {
  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  assert.equal(validateOffice(office), null);
  assert.ok(office.rooms.some((room) => room.name === "break room"));
  assert.ok(office.decor.some((item) => item.kind === "whiteboard" && /Timezone Buddy/.test(item.text)));
  assert.ok(office.decor.some((item) => item.kind === "coffee"));
  assert.equal(office.desks.length, 4);
  assert.ok(office.desks.some((desk) => desk.owner === "jules"));
  assert.equal(office.budget.furniture, 2);
  for (const desk of office.desks) {
    assert.ok(desk.items.length > 0);
  }
});

test("office schema rejects a missing break room", () => {
  const error = validateOffice({
    walls: "#000",
    floor: "tile",
    desks: [{ owner: "mira", x: 1, y: 1, items: ["monitor"] }],
    rooms: [{ name: "bullpen", x: 0, y: 0, w: 4, h: 4 }],
    decor: [{ kind: "whiteboard", x: 0, y: 0, text: "hi" }, { kind: "coffee", x: 1, y: 1 }],
  });
  assert.match(error, /break room/);
});
