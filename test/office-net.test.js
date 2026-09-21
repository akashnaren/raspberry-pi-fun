import { test } from "node:test";
import assert from "node:assert/strict";
import { FALLBACK_CAST, FALLBACK_OFFICE } from "../public/office-seed.js";
import {
  mergeStudioState,
  parseSocketMessage,
  reconnectDelayMs,
  usableOffice,
} from "../public/office-net.js";

test("reconnect backoff doubles and caps", () => {
  assert.equal(reconnectDelayMs(0), 1000);
  assert.equal(reconnectDelayMs(1), 2000);
  assert.equal(reconnectDelayMs(2), 4000);
  assert.equal(reconnectDelayMs(3), 8000);
  assert.equal(reconnectDelayMs(10), 30000);
  assert.equal(reconnectDelayMs(-2), 1000);
  assert.equal(reconnectDelayMs("nope"), 1000);
});

test("mergeStudioState keeps the seed office if /api/state is empty or torn", () => {
  const painted = { office: FALLBACK_OFFICE, employees: FALLBACK_CAST, dryRun: true };
  const fromNull = mergeStudioState(painted, null, FALLBACK_OFFICE, FALLBACK_CAST);
  assert.equal(fromNull.office, FALLBACK_OFFICE);
  assert.equal(fromNull.employees, FALLBACK_CAST);

  const fromEmpty = mergeStudioState(painted, { office: null, employees: [] }, FALLBACK_OFFICE, FALLBACK_CAST);
  assert.equal(usableOffice(fromEmpty.office), true);
  assert.equal(fromEmpty.office.rooms.length, FALLBACK_OFFICE.rooms.length);
  assert.equal(fromEmpty.employees.length, 4);

  const live = {
    office: { ...FALLBACK_OFFICE, walls: "#111111" },
    employees: FALLBACK_CAST,
    paused: true,
  };
  const merged = mergeStudioState(painted, live, FALLBACK_OFFICE, FALLBACK_CAST);
  assert.equal(merged.office.walls, "#111111");
  assert.equal(merged.paused, true);
});

test("socket messages that are not JSON do not throw", () => {
  assert.equal(parseSocketMessage("{"), null);
  assert.equal(parseSocketMessage(""), null);
  assert.equal(parseSocketMessage(undefined), null);
  assert.equal(parseSocketMessage('{"type":"hello"}').type, "hello");
});
