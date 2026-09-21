import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { validateOffice } from "../src/schemas.js";
import { REPO } from "./helpers.js";

test("seed office is a dollhouse the renderer can draw", async () => {
  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  assert.equal(validateOffice(office), null);
  assert.equal(office.coord_space, "topdown_norm");
  assert.ok(office.rooms.some((room) => room.name === "break room"));
  assert.ok(office.decor.some((item) => item.kind === "whiteboard" && /Timezone Buddy/.test(item.text)));
  assert.ok(office.decor.some((item) => item.kind === "coffee" || item.prop === "coffee_machine"));
  assert.ok(office.desks.length >= 3);
  assert.ok(office.desks.some((desk) => (desk.owner || desk.employee) === "nova"));
  assert.ok(office.desks.some((desk) => (desk.owner || desk.employee) === "kessler"));
  assert.ok(office.desks.some((desk) => (desk.owner || desk.employee) === "mira"));
  assert.equal(office.budget.furniture, 2);
  const vocab = JSON.parse(await readFile(join(REPO, "props-vocab.json"), "utf8"));
  const ids = new Set(vocab.props.map((item) => item.id));
  for (const desk of office.desks) {
    assert.ok(desk.items.includes("monitor"));
    assert.ok(desk.items.includes("mug") || desk.items.includes("coffee_mug"));
    assert.ok(desk.items.includes("plant_small") || desk.items.includes("plant"));
    for (const item of desk.items) {
      assert.ok(ids.has(item) || item === "coffee_mug" || item === "plant", item);
    }
  }
  assert.equal(office.desks.find((desk) => desk.owner === "nova").accent, "#F97316");
  assert.equal(office.desks.find((desk) => desk.owner === "kessler").accent, "#14B8A6");
  assert.equal(office.desks.find((desk) => desk.owner === "mira").accent, "#F59E0B");
});

test("office schema rejects a prop id outside Imagine vocab", () => {
  const office = {
    coord_space: "topdown_norm",
    desks: [{ owner: "nova", x: 0.2, y: 0.3, items: ["laser_cannon"] }],
    rooms: [{ name: "break room", x: 0, y: 0, w: 1, h: 1 }],
    decor: [{ kind: "whiteboard", x: 0, y: 0, text: "SHIP" }, { kind: "coffee", x: 1, y: 1 }],
  };
  assert.match(validateOffice(office), /unknown desk prop/);
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
