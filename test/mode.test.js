import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveStudioMode, runwayHours } from "../src/mode.js";
import { stubCall } from "../src/stubs.js";
import { createOrchestrator } from "../src/orchestrator.js";
import { createBudget } from "../src/budget.js";
import { createEventLog } from "../src/event-log.js";
import { createKillSwitch } from "../src/kill-switch.js";
import { createToolRunner } from "../src/tools.js";
import { tempStudioRoot } from "./helpers.js";
import { join } from "node:path";

test("DRY_RUN defaults true; live only with false plus a key", () => {
  assert.equal(resolveStudioMode({}).dryRun, true);
  assert.equal(resolveStudioMode({ DRY_RUN: "true", OPENROUTER_API_KEY: "sk" }).dryRun, true);
  assert.equal(resolveStudioMode({ DRY_RUN: "false", OPENROUTER_API_KEY: "sk" }).dryRun, false);
  assert.equal(resolveStudioMode({ DRY_RUN: "false" }).dryRun, true);
  assert.equal(resolveStudioMode({ STUDIO_MODE: "replay", OPENROUTER_API_KEY: "sk", DRY_RUN: "false" }).replay, true);
  assert.equal(resolveStudioMode({ STUDIO_MODE: "replay" }).dryRun, true);
});

test("runway is null until something is spent", () => {
  assert.equal(runwayHours({ spentUsd: 0, remainingUsd: 5 }), null);
  const hours = runwayHours({
    spentUsd: 2.5,
    remainingUsd: 2.5,
    now: Date.parse("2026-09-21T12:00:00Z"),
  });
  assert.equal(hours, 12);
});

test("replay mode never calls the model", async () => {
  const root = await tempStudioRoot();
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  await events.append({ type: "say", actor: "nova", data: { text: "hi" } });
  let called = 0;
  const orchestrator = createOrchestrator({
    workspaceRoot: join(root, "workspace"),
    employees: [{ id: "nova", name: "Nova", role: "programmer", model: "qwen/qwen3-coder-next" }],
    lottery: { nova: 1 },
    tick: { minMs: 1000, maxMs: 1000 },
    events,
    tools: { execute: async () => ({ ok: true }) },
    llm: {
      dryRun: true,
      complete: async () => {
        called += 1;
        return { toolCalls: [], costUsd: 1, dryRun: false };
      },
    },
    budget: { snapshot: () => ({ exhausted: false }) },
    killSwitch: { paused: async () => false },
    replay: true,
  });
  const result = await orchestrator.tickOnce();
  assert.equal(result.skipped, "replay");
  assert.equal(called, 0);
});

test("ceiling pause can emit a local stub without charging", async () => {
  const root = await tempStudioRoot();
  const workspaceRoot = join(root, "workspace");
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const budget = await createBudget({
    filePath: join(root, "data", "spend.json"),
    dailyCeilingUsd: 5,
  });
  await budget.recordSpend(5);
  const killSwitch = await createKillSwitch({ filePath: join(root, "data", "PAUSED") });
  const tools = createToolRunner({ workspaceRoot, events });
  const orchestrator = createOrchestrator({
    workspaceRoot,
    employees: [{ id: "jules", name: "Jules Park", role: "office_manager", model: "meta-llama/llama-4-scout" }],
    lottery: { jules: 1 },
    tick: { minMs: 1000, maxMs: 1000 },
    events,
    tools,
    llm: {
      dryRun: true,
      complete: async () => {
        throw new Error("should not call");
      },
    },
    budget,
    killSwitch,
    localStubs: true,
    random: () => 0,
  });
  const first = await orchestrator.tickOnce();
  assert.equal(first.skipped, "budget");
  assert.equal(events.all().some((event) => event.type === "say" && event.actor === "jules"), true);
  assert.equal(events.all().some((event) => event.type === "budget_paused"), true);
  const second = await orchestrator.tickOnce();
  assert.equal(second.skipped, "budget");
  assert.equal(stubCall({ id: "jules" }).arguments.message.includes("Lights down"), true);
});
