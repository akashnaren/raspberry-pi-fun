import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createEventLog } from "../src/event-log.js";
import { createToolRunner, filterSay } from "../src/tools.js";
import { tempStudioRoot } from "./helpers.js";

test("say filter strips markup and caps length", () => {
  assert.equal(filterSay("<b>hi</b>"), "hi");
  assert.equal(filterSay("x".repeat(300)).endsWith("..."), true);
});

test("tools sandbox writes, backlog, journal, and refuse machinery", async () => {
  const root = await tempStudioRoot();
  const workspaceRoot = join(root, "workspace");
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const tools = createToolRunner({ workspaceRoot, events });

  const read = await tools.execute("nova", "read_file", { path: "strategy.md" });
  assert.equal(read.ok, true);
  assert.match(read.contents, /Timezone Buddy/);

  const added = await tools.execute("mira", "add_task", { text: "Tighten copy on the epoch field" });
  assert.equal(added.task.id, "t-3");
  const closed = await tools.execute("kessler", "close_task", { id: "t-1" });
  assert.equal(closed.task.status, "closed");

  const said = await tools.execute("mira", "say", { message: "Keep it one screen." });
  assert.equal(said.ok, true);
  const note = await tools.execute("nova", "journal", { text: "Read the seed." });
  assert.equal(note.ok, true);
  const journal = await readFile(join(workspaceRoot, "employees/nova/journal.md"), "utf8");
  assert.match(journal, /Read the seed/);

  const blocked = await tools.execute("nova", "read_file", { path: "../src/index.js" });
  assert.equal(blocked.ok, false);
  const machine = await tools.execute("nova", "write_file", {
    path: "src/hack.js",
    contents: "nope",
  });
  assert.equal(machine.ok, false);
});
