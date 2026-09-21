import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { FALLBACK_CAST, FALLBACK_OFFICE, seedSprites } from "../public/office-seed.js";
import { lookOf, windowSky } from "../public/office-draw.js";
import { reconnectDelayMs } from "../public/office-net.js";
import { validateOffice } from "../src/schemas.js";
import { REPO } from "./helpers.js";

test("fallback office paints a room before any socket", async () => {
  assert.equal(validateOffice(FALLBACK_OFFICE), null);
  assert.equal(FALLBACK_CAST.length, 4);
  assert.deepEqual(
    FALLBACK_CAST.map((person) => person.id).sort(),
    ["jules", "kessler", "mira", "nova"],
  );
  const sprites = seedSprites();
  assert.equal(sprites.size, 4);
  for (const person of FALLBACK_CAST) {
    const sprite = sprites.get(person.id);
    assert.ok(sprite, person.id);
    assert.equal(sprite.pose, "sit");
    assert.equal(sprite.at, "desk");
    assert.doesNotMatch(sprite.pose, /think/i);
    const look = lookOf(person);
    assert.ok(look.top);
    assert.ok(look.hair);
    assert.ok(look.skin);
    assert.ok(look.bottomKind);
  }
  assert.ok(FALLBACK_OFFICE.decor.some((item) => item.kind === "window"));
  const plants = FALLBACK_OFFICE.decor.filter((item) => item.kind === "plant");
  assert.ok(plants.length >= 5);
  assert.equal(FALLBACK_OFFICE.decor.find((item) => item.kind === "whiteboard").text, "SHIP: kanban · units");
  assert.ok(windowSky(false).top);
  assert.notEqual(windowSky(false).top, windowSky(true).top);
  const looks = Object.fromEntries(FALLBACK_CAST.map((person) => [person.id, lookOf(person)]));
  assert.equal(looks.nova.topKind, "hoodie");
  assert.equal(looks.kessler.accessory, "glasses");
  assert.equal(looks.mira.topKind, "blazer");
  assert.equal(looks.jules.topKind, "cardigan");
  assert.notEqual(looks.nova.top, looks.mira.top);
  assert.notEqual(looks.jules.top, looks.kessler.top);
  assert.equal(looks.nova.face, "oval-pony");
  assert.equal(looks.kessler.face, "square-crop");
  assert.equal(looks.mira.face, "long-part");
  assert.equal(looks.jules.face, "round-wave");
  assert.equal(new Set(Object.values(looks).map((look) => look.face)).size, 4);
});

test("workspace office and fallback stay aligned on the load-bearing bits", async () => {
  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  assert.equal(office.desks.length, FALLBACK_OFFICE.desks.length);
  assert.equal(office.rooms.length, FALLBACK_OFFICE.rooms.length);
  for (const id of ["nova", "kessler", "mira", "jules"]) {
    const a = office.desks.find((desk) => desk.owner === id);
    const b = FALLBACK_OFFICE.desks.find((desk) => desk.owner === id);
    assert.equal(a.x, b.x);
    assert.equal(a.y, b.y);
  }
  const hud = await readFile(join(REPO, "public/index.html"), "utf8");
  assert.match(hud, /Day 1/);
  assert.match(hud, /4 people/);
  assert.match(hud, /id="pause-btn"/);
  assert.match(hud, /class="kill"/);
  assert.match(hud, /id="pause-note"/);
  assert.doesNotMatch(hud, /ticker|ON AIR|Meridian Desk/);
  const draw = await readFile(join(REPO, "public/office-draw.js"), "utf8");
  assert.match(draw, /drawDeskLamp/);
  assert.match(draw, /drawKeyboard/);
  assert.match(draw, /255,224,138/);
  assert.match(draw, /oval-pony|square-crop|long-part|round-wave/);
  assert.match(draw, /whiteboardPx/);
  const officeJs = await readFile(join(REPO, "public/office.js"), "utf8");
  assert.match(officeJs, /FALLBACK_OFFICE/);
  assert.match(officeJs, /hydrateFromHttp/);
  assert.match(officeJs, /\/api\/state/);
  assert.match(officeJs, /reconnectDelayMs/);
  assert.match(officeJs, /mergeStudioState/);
  assert.match(officeJs, /facePairs/);
  assert.match(officeJs, /settlePose/);
  assert.match(draw, /sitting|pose === "sit"/);
  assert.match(officeJs, /requestAnimationFrame\(loop\)/);
  const loopAt = officeJs.indexOf("requestAnimationFrame(loop)");
  const connectAt = officeJs.lastIndexOf("connect()");
  assert.ok(loopAt > 0 && connectAt > loopAt, "paint loop starts before websocket connect");
  assert.equal(reconnectDelayMs(0) < reconnectDelayMs(3), true);
});
