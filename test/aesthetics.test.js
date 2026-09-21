import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { applyAestheticsPatch, loadEmployeeRecord, validateAesthetics } from "../src/aesthetics.js";
import { createEventLog } from "../src/event-log.js";
import { createToolRunner } from "../src/tools.js";
import { tempStudioRoot } from "./helpers.js";

const roster = [
  { id: "nova", name: "Nova Chen", role: "programmer" },
  { id: "reed", name: "Reed Park", role: "office_manager" },
  { id: "mira", name: "Mira Sol", role: "producer" },
];

test("wardrobe allowlist rejects clothes you do not own", () => {
  const error = validateAesthetics(
    {
      skin: "warm",
      hair: "short-black",
      outfit: { top: "hoodie", bottom: "jeans", shoes: "sneakers", accessory: "earbuds" },
      desk_style: "messy",
    },
    ["tee", "jeans", "sneakers", "none"],
  );
  assert.match(error, /hoodie/);
});

test("only you can change your look; only Reed edits the office", async () => {
  const root = await tempStudioRoot();
  const workspaceRoot = join(root, "workspace");
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const tools = createToolRunner({ workspaceRoot, events, employees: roster });

  const peer = await tools.execute("reed", "write_file", {
    path: "employees/nova.json",
    contents: "{}",
  });
  assert.equal(peer.ok, false);

  const mine = await tools.execute("nova", "edit_self_aesthetics", {
    outfit: { top: "tee" },
  });
  assert.equal(mine.ok, true);
  const nova = await loadEmployeeRecord(workspaceRoot, "nova");
  assert.equal(nova.aesthetics.outfit.top, "tee");

  const stolen = await tools.execute("mira", "write_file", {
    path: "employees/nova.json",
    contents: JSON.stringify(nova),
  });
  assert.equal(stolen.ok, false);

  const trespass = await tools.execute("nova", "edit_office", {
    office: { walls: "#000000" },
  });
  assert.equal(trespass.ok, false);

  const moved = await tools.execute("reed", "edit_office", {
    office: {
      decor: JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8")).decor.concat([
        { kind: "plant", x: 18, y: 8 },
      ]),
    },
  });
  assert.equal(moved.ok, true);
  const office = JSON.parse(await readFile(join(workspaceRoot, "office.json"), "utf8"));
  assert.equal(office.budget.furniture, 1);

  const asked = await tools.execute("nova", "request", {
    item: "standing_desk",
    reason: "I ship faster on my feet.",
  });
  assert.equal(asked.ok, true);
  assert.equal(asked.request.item, "standing_desk");
});

test("applyAestheticsPatch keeps a valid person", async () => {
  const root = await tempStudioRoot();
  const record = await loadEmployeeRecord(join(root, "workspace"), "kessler");
  const next = applyAestheticsPatch(record, { outfit: { accessory: "watch" } });
  assert.equal(next.aesthetics.outfit.accessory, "watch");
  assert.equal(next.id, "kessler");
});
