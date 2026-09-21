import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyOfficeTweak, isOfficeTweak, validateOffice } from "../src/schemas.js";
import { REPO } from "./helpers.js";

test("seed office is a dollhouse the renderer can draw", async () => {
  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  assert.equal(validateOffice(office), null);
  assert.ok(office.rooms.some((room) => room.name === "break room"));
  assert.ok(office.rooms.some((room) => room.name === "meeting"));
  assert.ok(office.decor.some((item) => item.kind === "whiteboard" && /invoice|notes/.test(item.text)));
  assert.ok(office.decor.some((item) => item.kind === "coffee"));
  assert.ok(office.decor.some((item) => item.kind === "window"));
  assert.ok(office.decor.some((item) => item.kind === "plant"));
  assert.equal(office.desks.length, 4);
  assert.ok(office.desks.some((desk) => desk.owner === "jules"));
  assert.equal(office.budget.furniture, 2);
  for (const desk of office.desks) {
    assert.ok(desk.items.includes("monitor"));
    assert.ok(desk.items.includes("coffee_mug"));
    assert.ok(desk.items.includes("plant"));
  }
});

test("Jules tweaks move a plant without spending furniture credits", async () => {
  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  const before = office.decor.find((item) => item.kind === "plant" && item.x === 8 && item.y === 9);
  assert.ok(before);
  assert.equal(isOfficeTweak({ move: { kind: "plant", from: { x: 8, y: 9 }, x: 19, y: 10 } }), true);
  assert.equal(isOfficeTweak({ office: { walls: "#000" } }), false);
  const { office: next, changes } = applyOfficeTweak(office, {
    move: { kind: "plant", from: { x: 8, y: 9 }, x: 19, y: 10 },
  });
  assert.equal(validateOffice(next), null);
  assert.equal(next.budget.furniture, 2);
  assert.equal(next.decor.length, office.decor.length);
  assert.ok(next.decor.some((item) => item.kind === "plant" && item.x === 19 && item.y === 10));
  assert.equal(next.decor.some((item) => item.kind === "plant" && item.x === 8 && item.y === 9), false);
  assert.equal(changes[0].kind, "plant");
  const board = applyOfficeTweak(office, { whiteboard: "SHIP: Docs. Print. Download." });
  assert.match(board.office.decor.find((item) => item.kind === "whiteboard").text, /Print/);
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
