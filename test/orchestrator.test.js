import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { createBudget } from "../src/budget.js";
import { createEventLog } from "../src/event-log.js";
import { createKillSwitch } from "../src/kill-switch.js";
import { createOrchestrator, nextTickDelay, pickEmployee } from "../src/orchestrator.js";
import { createToolRunner } from "../src/tools.js";
import { tempStudioRoot } from "./helpers.js";

const employees = [
  { id: "mira", name: "Mira", role: "producer", model: "z-ai/glm-5.3-flash", priorities: "scope" },
  { id: "nova", name: "Nova", role: "programmer", model: "qwen/qwen3-coder-next", priorities: "ship" },
  { id: "kessler", name: "Kessler", role: "qa", model: "nousresearch/hermes-3-llama-3.1-70b", priorities: "bugs" },
];

test("lottery picks one employee and tick delay stays in 90–120s", () => {
  const picks = new Set();
  for (let i = 0; i < 20; i += 1) {
    picks.add(pickEmployee(employees, { mira: 1, nova: 1, kessler: 1 }, () => i / 20).id);
  }
  assert.equal(picks.size >= 2, true);
  const delay = nextTickDelay(90_000, 120_000, () => 0.5);
  assert.equal(delay >= 90_000 && delay <= 120_000, true);
});

test("one tick runs one employee and respects pause plus ceiling", async () => {
  const root = await tempStudioRoot();
  const workspaceRoot = join(root, "workspace");
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const budget = await createBudget({
    filePath: join(root, "data", "spend.json"),
    dailyCeilingUsd: 5,
  });
  const killSwitch = await createKillSwitch({ filePath: join(root, "data", "PAUSED") });
  const tools = createToolRunner({ workspaceRoot, events });
  const llm = {
    dryRun: true,
    async complete({ employee }) {
      return {
        toolCalls: [{ name: "say", arguments: { message: `${employee.name} checking in.` } }],
        costUsd: 0,
        dryRun: true,
        model: "dry-run",
      };
    },
  };
  const orchestrator = createOrchestrator({
    workspaceRoot,
    employees,
    lottery: { mira: 1, nova: 0, kessler: 0 },
    tick: { minMs: 90_000, maxMs: 120_000 },
    events,
    tools,
    llm,
    budget,
    killSwitch,
    random: () => 0,
  });

  const first = await orchestrator.tickOnce();
  assert.equal(first.employee.id, "mira");
  assert.equal(events.all().some((event) => event.type === "say" && event.actor === "mira"), true);

  await killSwitch.pause("test");
  const paused = await orchestrator.tickOnce();
  assert.equal(paused.skipped, "paused");
  await killSwitch.resume();

  await budget.recordSpend(5);
  const capped = await orchestrator.tickOnce();
  assert.equal(capped.skipped, "budget");
});
