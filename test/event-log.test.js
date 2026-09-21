import { test } from "node:test";
import assert from "node:assert/strict";
import { appendFile, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createEventLog, parseJsonl } from "../src/event-log.js";

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

test("parseJsonl skips null bytes and broken lines without throwing", () => {
  const good = JSON.stringify({ id: "e-000001", type: "say", actor: "mira" });
  const raw = Buffer.from(`${good}\n{"nope":\n${good}\u0000truncated\n{"ok":true}\n`, "utf8");
  const parsed = parseJsonl(raw);
  assert.equal(parsed.events.length, 2);
  assert.equal(parsed.events[0].id, "e-000001");
  assert.ok(parsed.quarantined.some((item) => item.reason === "null-byte"));
  assert.ok(parsed.quarantined.some((item) => item.reason === "json"));
});

test("corrupt JSONL after a power cut does not crash the log", async () => {
  const dir = await mkdtemp(join(tmpdir(), "events-"));
  const filePath = join(dir, "events.jsonl");
  const good = JSON.stringify({ id: "e-000004", type: "say", actor: "jules", message: "coffee" });
  const smashed = Buffer.concat([
    Buffer.from(`${good}\n`, "utf8"),
    Buffer.from([0x00, 0x00, 0x00]),
    Buffer.from('{"id":"e-000005","type":"say",', "utf8"),
    Buffer.from("\n"),
    Buffer.from(`${JSON.stringify({ id: "e-000006", type: "journal", actor: "nova" })}\n`, "utf8"),
  ]);
  await writeFile(filePath, smashed);
  const log = await createEventLog({ filePath, now: () => 50 });
  assert.equal(log.all().length, 2);
  assert.equal(log.all()[0].id, "e-000004");
  assert.equal(log.all()[1].id, "e-000006");
  assert.ok(log.quarantined().length >= 1);
  const next = await log.append({ type: "say", actor: "mira", message: "still here" });
  assert.equal(next.id, "e-000007");
  const quarantine = await readFile(`${filePath}.corrupt`, "utf8");
  assert.match(quarantine, /null-byte|json/);
});

test("empty or missing log is fine; append after quarantine still writes", async () => {
  const dir = await mkdtemp(join(tmpdir(), "events-"));
  const filePath = join(dir, "events.jsonl");
  await writeFile(filePath, "\n\n\u0000\nnot-json\n");
  const log = await createEventLog({ filePath, now: () => 1 });
  assert.equal(log.all().length, 0);
  await log.append({ type: "world_started", actor: "system" });
  assert.equal(log.all().length, 1);
  await appendFile(filePath, "\u0000dead\n", "utf8");
  const again = await createEventLog({ filePath, now: () => 2 });
  assert.equal(again.all().some((event) => event.type === "world_started"), true);
});
