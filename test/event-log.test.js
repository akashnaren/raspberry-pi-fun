import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createEventLog } from "../src/event-log.js";

test("event log appends, numbers, and reloads", async () => {
  const dir = await mkdtemp(join(tmpdir(), "events-"));
  const filePath = join(dir, "events.jsonl");
  let ts = 1000;
  const log = await createEventLog({ filePath, now: () => (ts += 1) });
  await log.append({ type: "say", actor: "mira", message: "hello" });
  await log.append({ type: "journal", actor: "nova", message: "notes" });
  assert.equal(log.all().length, 2);
  assert.equal(log.recent(1)[0].id, "e-000002");
  assert.equal(log.recent(1)[0].type, "journal");

  const reloaded = await createEventLog({ filePath, now: () => 9 });
  assert.equal(reloaded.all().length, 2);
  const third = await reloaded.append({ type: "say", actor: "kessler", message: "again" });
  assert.equal(third.id, "e-000003");
});
