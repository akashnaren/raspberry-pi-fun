import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { FALLBACK_CAST, FALLBACK_OFFICE } from "../public/office-seed.js";
import { clutterPx, drawOffice, limbPose, lookOf, staticRoomKey } from "../public/office-draw.js";
import { dprFor, dueBlink, frameGapMs, hotKind } from "../public/office-perf.js";
import { REPO } from "./helpers.js";

const CAST_TOP = {
  nova: "#F97316",
  kessler: "#14B8A6",
  mira: "#F59E0B",
  jules: "#6366F1",
};

test("cast garments read as coral, teal, amber, indigo — never Reed, never #8B5CF6", async () => {
  for (const person of FALLBACK_CAST) {
    assert.equal(lookOf(person).top, CAST_TOP[person.id], person.id);
    assert.equal(person.accent, CAST_TOP[person.id]);
  }
  const files = ["public/office-draw.js", "public/office.js", "public/office.css", "public/index.html", "public/office-seed.js"];
  const blob = (await Promise.all(files.map((file) => readFile(join(REPO, file), "utf8")))).join("\n");
  assert.doesNotMatch(blob, /8B5CF6/);
  assert.doesNotMatch(blob, /Reed/);
});

test("sit bends at the hip, walk strides, stand does not bob", () => {
  const sit = limbPose("sit", 0);
  assert.equal(sit.posture, "sit");
  assert.ok(sit.thigh > 0 && sit.shin > 0);
  assert.equal(sit.bob, 0);
  const a = limbPose("walk", 0);
  const b = limbPose("walk", 1);
  assert.equal(a.posture, "walk");
  assert.ok(a.armL * b.armL < 0);
  assert.notEqual(a.thighL, b.thighL);
  const stand = limbPose("stand", 0);
  assert.equal(stand.posture, "stand");
  assert.equal(stand.bob, 0);
  assert.equal(limbPose("stand-talk", 1).posture, "stand");
  assert.equal(limbPose("sit-type", 1).posture, "sit");
});

test("room cache key ignores sprites and tracks the baked office", () => {
  const base = { office: FALLBACK_OFFICE, cell: 32, dim: false, w: 1000, h: 800, ox: 12, oy: 8, employees: FALLBACK_CAST };
  const again = staticRoomKey(base);
  assert.equal(again, staticRoomKey(base));
  const board = structuredClone(FALLBACK_OFFICE);
  board.decor = board.decor.map((item) => (item.kind === "whiteboard" ? { ...item, text: "SHIP: other" } : item));
  assert.notEqual(staticRoomKey({ ...base, office: board }), again);
  const plant = structuredClone(FALLBACK_OFFICE);
  plant.decor = plant.decor.map((item) => (item.kind === "plant" && item.x === 8 ? { ...item, x: 9 } : item));
  assert.notEqual(staticRoomKey({ ...base, office: plant }), again);
  assert.notEqual(staticRoomKey({ ...base, dim: true }), again);
});

test("DPR stays 1 and idle does not schedule another frame", () => {
  assert.equal(dprFor(2), 1);
  assert.equal(dprFor(1), 1);
  assert.equal(frameGapMs("idle", 30), 0);
  assert.equal(frameGapMs("hover", 0), 0);
  assert.equal(frameGapMs("type", 1), 140);
  assert.equal(frameGapMs("walk", 4), 16);
  assert.equal(frameGapMs("walk", 18), 33);
  assert.equal(frameGapMs("talk", 20), 33);
  assert.equal(hotKind({ sprites: [{ pose: "sit", path: [] }] }), "idle");
  assert.equal(hotKind({ sprites: [{ pose: "walk", path: [] }] }), "walk");
  assert.equal(hotKind({ sprites: [{ pose: "sit", path: [{ x: 2, y: 3 }] }] }), "walk");
  assert.equal(hotKind({ bubble: { born: 0, life: 100 }, now: 40, sprites: [{ pose: "sit", path: [] }] }), "talk");
  assert.equal(hotKind({ sprites: [{ pose: "sit-type", path: [] }] }), "type");
  const blink = dueBlink(0, [{ x: 4, blinkUntil: 0 }]);
  assert.ok(blink.delay >= 90);
  assert.equal(blink.index, 0);
});

test("floor seams are batched and desk clutter has a TV-scale minimum", () => {
  assert.ok(clutterPx(20) >= 12);
  assert.ok(clutterPx(48) >= 18);
  const counts = { stroke: 0, fill: 0, fillRect: 0 };
  const gradient = () => ({ addColorStop() {} });
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    globalAlpha: 1,
    beginPath() {},
    closePath() {},
    moveTo() {},
    lineTo() {},
    arc() {},
    arcTo() {},
    ellipse() {},
    quadraticCurveTo() {},
    fill() {
      counts.fill += 1;
    },
    stroke() {
      counts.stroke += 1;
    },
    fillRect() {
      counts.fillRect += 1;
    },
    strokeRect() {},
    fillText() {},
    measureText(text) {
      return { width: String(text).length * 8 };
    },
    createLinearGradient: gradient,
    createRadialGradient: gradient,
    save() {},
    restore() {},
    translate() {},
    scale() {},
  };
  drawOffice(ctx, {
    office: FALLBACK_OFFICE,
    employees: FALLBACK_CAST,
    ox: 24,
    oy: 20,
    cell: 32,
    dim: false,
  });
  assert.ok(counts.stroke < 48, `strokes ${counts.stroke}`);
});

test("renderer stays 2D, cached, and event-driven — no WebGL, no worker", async () => {
  const draw = await readFile(join(REPO, "public/office-draw.js"), "utf8");
  const office = await readFile(join(REPO, "public/office.js"), "utf8");
  const perf = await readFile(join(REPO, "public/office-perf.js"), "utf8");
  assert.match(draw, /softShadow/);
  assert.match(draw, /staticRoomKey/);
  assert.doesNotMatch(draw, /Math\.sin\(now/);
  assert.match(draw, /No WebGL/);
  assert.match(office, /staticRoomKey/);
  assert.match(office, /frameGapMs/);
  assert.match(office, /dprFor\(/);
  assert.match(office, /requestAnimationFrame\(loop\)/);
  assert.doesNotMatch(`${draw}\n${office}\n${perf}`, /getContext\(\s*["']webgl|new Worker\(/);
});
