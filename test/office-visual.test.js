import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { FALLBACK_CAST, FALLBACK_OFFICE } from "../public/office-seed.js";
import { clutterPx, drawBubble, drawOffice, limbPose, lookOf, staticRoomKey } from "../public/office-draw.js";
import { armFrame, bubbleAlive, dprFor, dueBlink, frameGapMs, hotKind, perfReadout, TYPE_BURST_MS, typingPose } from "../public/office-perf.js";
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

test("a Date.now bubble is not talk on the animation-frame clock", () => {
  const epoch = 1_790_048_991_039;
  assert.equal(bubbleAlive(1000, epoch, 5120), false);
  assert.equal(bubbleAlive(40, 0, 100), true);
  assert.equal(bubbleAlive(100, 0, 100), false);
  assert.equal(
    hotKind({
      now: 1000,
      bubble: { born: epoch, life: 5120 },
      sprites: [{ pose: "sit", path: [] }],
    }),
    "idle",
  );
  assert.equal(
    hotKind({
      now: 5120,
      bubble: { born: 0, life: 5120 },
      sprites: [{ pose: "sit", path: [] }],
    }),
    "idle",
  );
  assert.equal(
    drawBubble(mockCtx(), {
      bubble: { born: epoch, lines: ["Coffee."], color: "#fff" },
      sprite: { x: 2, y: 3 },
      ox: 0,
      oy: 0,
      cell: 30,
      now: 80,
      enter: 220,
      hold: 4200,
      fade: 700,
    }),
    false,
  );
  assert.equal(
    drawBubble(mockCtx(), {
      bubble: { born: 0, lines: ["Coffee."], color: "#c4b8a8" },
      sprite: { x: 2, y: 3 },
      ox: 0,
      oy: 0,
      cell: 30,
      now: 300,
      enter: 220,
      hold: 4200,
      fade: 700,
    }),
    true,
  );
});

test("idle stops the frame clock; a wide gap does not chain animation frames", () => {
  assert.deepEqual(armFrame({ kind: "idle", lastFrameMs: 30, sinceDrawMs: 0 }), {
    mode: "idle",
    gap: 0,
    waitMs: 0,
  });
  assert.deepEqual(armFrame({ kind: "hover", sinceDrawMs: 0 }), { mode: "idle", gap: 0, waitMs: 0 });
  assert.deepEqual(armFrame({ kind: "walk", lastFrameMs: 4, sinceDrawMs: 16 }), {
    mode: "frame",
    gap: 16,
    waitMs: 0,
  });
  assert.deepEqual(armFrame({ kind: "walk", lastFrameMs: 4, sinceDrawMs: 0 }), {
    mode: "frame",
    gap: 16,
    waitMs: 0,
  });
  assert.deepEqual(armFrame({ kind: "walk", lastFrameMs: 18, sinceDrawMs: 10 }), {
    mode: "wait",
    gap: 33,
    waitMs: 23,
  });
  assert.deepEqual(armFrame({ kind: "type", lastFrameMs: 4, sinceDrawMs: 20 }), {
    mode: "wait",
    gap: 140,
    waitMs: 120,
  });
  assert.deepEqual(armFrame({ kind: "type", lastFrameMs: 4, sinceDrawMs: 140 }), {
    mode: "frame",
    gap: 140,
    waitMs: 0,
  });
  assert.deepEqual(armFrame({ kind: "talk", lastFrameMs: 20, sinceDrawMs: 40 }), {
    mode: "frame",
    gap: 33,
    waitMs: 0,
  });
});

test("typing is a short burst, then the seated pose is still", () => {
  const fresh = typingPose({ pose: "sit-type", at: "desk", frame: 0, typeUntil: 0 }, 1000);
  assert.equal(fresh.hot, true);
  assert.equal(fresh.pose, "sit-type");
  assert.equal(fresh.typeUntil, 1000 + TYPE_BURST_MS);
  assert.ok(TYPE_BURST_MS <= 2500);
  const done = typingPose({ pose: "sit-type", at: "desk", frame: 1, typeUntil: 1000 }, 1000 + TYPE_BURST_MS);
  assert.equal(done.hot, false);
  assert.equal(done.pose, "settle");
  assert.equal(done.typeUntil, 0);
  assert.equal(done.frame, 0);
});

test("perf readout reports idle as stopped and walk as frames", () => {
  assert.deepEqual(perfReadout({ kind: "idle" }), { fps: 0, gap: 0, text: "idle · 0 fps · gap 0" });
  assert.equal(perfReadout({ kind: "hover" }).fps, 0);
  assert.deepEqual(perfReadout({ kind: "walk", gap: 16, frames: 30, spanMs: 500 }), {
    fps: 60,
    gap: 16,
    text: "walk · 60 fps · gap 16",
  });
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

test("painted diorama: upright billboards, pose library, quiet floor, soft lamps", async () => {
  const draw = await import("../public/office-draw.js");
  assert.deepEqual(draw.HUMAN_LAYERS, ["body", "hair", "bottom", "top", "shoes", "accessory"]);
  assert.equal(draw.dioramaPose("sit-type", 0, "desk"), "sit_type");
  assert.equal(draw.dioramaPose("type", 1, "desk"), "sit_type");
  assert.equal(draw.dioramaPose("sit", 0, "desk"), "sit_think_hand");
  assert.equal(draw.dioramaPose("sit-talk", 0, "couch"), "sit_think_hand");
  assert.equal(draw.dioramaPose("walk", 0, "desk"), "walk_1");
  assert.equal(draw.dioramaPose("walk", 1, "coffee"), "walk_2");
  assert.equal(draw.dioramaPose("stand", 0, "coffee"), "stand_coffee");
  assert.equal(draw.dioramaPose("stand-talk", 0, "coffee"), "stand_coffee");
  assert.equal(draw.dioramaPose("stand", 0, "whiteboard"), "stand_whiteboard");
  assert.equal(draw.dioramaPose("talk", 1, "whiteboard"), "stand_whiteboard");
  assert.equal(draw.dioramaPose("stand-talk", 0, "meeting"), "stand_point");
  assert.equal(draw.dioramaPose("stand", 0, "desk"), "stand_point");
  assert.ok(draw.LAMP_POOL_ALPHA <= 0.25);
  assert.ok(draw.lampColor(false).includes("255,224,138"));
  const lampAlpha = Number(draw.lampColor(false).match(/[\d.]+\)/)[0].replace(")", ""));
  assert.ok(lampAlpha <= 0.25 && lampAlpha > 0);
  assert.ok(draw.lampColor(true).length > 0);
  assert.equal(draw.FLOOR_GRID_ALPHA, 0);
  assert.ok(draw.FLOOR_GRID_ALPHA <= 0.2);
  assert.equal(draw.floorMaterial("bullpen"), "wood");
  assert.equal(draw.floorMaterial("break room"), "wood");
  assert.equal(draw.floorMaterial("meeting"), "concrete");
  const plate = draw.deskPlate(40, "jules");
  assert.ok(plate.side >= 40 * 0.28, "desk has a thick side face");
  assert.ok(plate.top > 0, "desk has a top");
  assert.ok(plate.wide > draw.deskPlate(40, "nova").wide);
  assert.ok(draw.whiteboardPx(32) >= 34);
  assert.ok(draw.whiteboardPx(48) > 48 * 0.72);
  const bill = draw.billboardMetrics(40);
  assert.equal(bill.upright, true);
  assert.ok(bill.height > bill.width * 1.35, "billboard is upright, not a pancake");
  assert.ok(bill.height > 40 * 2);
  const order = draw.depthOrder([
    { id: "back", sprite: { y: 9, x: 2 } },
    { id: "front", sprite: { y: 4, x: 8 } },
    { id: "front-right", sprite: { y: 4, x: 3 } },
  ]);
  assert.deepEqual(
    order.map((row) => row.id),
    ["front-right", "front", "back"],
  );
  const layers = [];
  const ctx = mockCtx();
  ctx.markLayer = (name) => layers.push(name);
  draw.drawPerson(ctx, {
    employee: FALLBACK_CAST[0],
    sprite: { x: 4, y: 6, pose: "sit-type", frame: 1, facing: 1, at: "desk", active: 0, blinkUntil: 0 },
    ox: 10,
    oy: 10,
    cell: 36,
    now: 0,
    hover: false,
  });
  assert.deepEqual(layers, draw.HUMAN_LAYERS);
  const src = await readFile(join(REPO, "public/office-draw.js"), "utf8");
  assert.doesNotMatch(src, /function drawTiles|drawPlanks/);
  assert.match(src, /softShadow/);
  const office = await readFile(join(REPO, "public/office.js"), "utf8");
  assert.match(office, /dioramaPose/);
  assert.match(office, /depthOrder/);
  assert.match(office, /staticRoomKey/);
  assert.match(office, /frameGapMs/);
});

function mockCtx() {
  const gradient = () => ({ addColorStop() {} });
  return {
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
    fill() {},
    stroke() {},
    fillRect() {},
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
    rotate() {},
  };
}

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
  assert.match(office, /armFrame/);
  assert.match(office, /typingPose/);
  assert.match(office, /born:\s*performance\.now\(\)/);
  assert.doesNotMatch(office, /born:\s*Date\.now\(\)/);
  assert.match(office, /get\("perf"\) === "1"/);
  assert.doesNotMatch(office, /mode === "wait"[\s\S]{0,80}requestAnimationFrame/);
  assert.doesNotMatch(`${draw}\n${office}\n${perf}`, /getContext\(\s*["']webgl|new Worker\(/);
});
