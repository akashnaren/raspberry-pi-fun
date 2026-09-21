import { readFile } from "node:fs/promises";
import { assembleContext, repeatingPattern } from "./context.js";
import { resolveWorkspacePath } from "./paths.js";
import { filterSay } from "./tools.js";

export function pickEmployee(employees, lottery, random) {
  const weights = employees.map((employee) => ({
    employee,
    weight: Math.max(1, Number(lottery?.[employee.id]) || 1),
  }));
  const total = weights.reduce((sum, item) => sum + item.weight, 0);
  let roll = random() * total;
  for (const item of weights) {
    roll -= item.weight;
    if (roll <= 0) return item.employee;
  }
  return weights[weights.length - 1].employee;
}

export function nextTickDelay(minMs, maxMs, random) {
  const min = Math.max(250, Number(minMs) || 90_000);
  const max = Math.max(min, Number(maxMs) || 120_000);
  return Math.floor(min + random() * (max - min + 1));
}

export function createOrchestrator({
  workspaceRoot,
  employees,
  lottery,
  tick,
  events,
  tools,
  llm,
  budget,
  killSwitch,
  now = () => Date.now(),
  random = Math.random,
  onTickScheduled,
}) {
  let timer = null;
  let stopped = false;
  let running = false;

  async function readDoc(rel) {
    try {
      const { abs } = resolveWorkspacePath(workspaceRoot, rel);
      return await readFile(abs, "utf8");
    } catch {
      return "";
    }
  }

  async function tickOnce() {
    if (running) return { skipped: "in-flight" };
    if (await killSwitch.paused()) {
      await events.append({
        type: "world_paused",
        actor: "system",
        message: "world paused (kill switch)",
        data: { reason: "kill-switch" },
      });
      return { skipped: "paused" };
    }
    const snap = budget.snapshot();
    if (snap.exhausted) {
      await events.append({
        type: "budget_paused",
        actor: "system",
        message: `daily ceiling hit ($${snap.ceilingUsd.toFixed(2)}). world waits until ${nextUtcDay(now())}`,
        data: snap,
      });
      return { skipped: "budget" };
    }

    running = true;
    const employee = pickEmployee(employees, lottery, random);
    await events.append({
      type: "turn_started",
      actor: employee.id,
      message: `${employee.name} takes a turn`,
      data: { role: employee.role, model: employee.model, dryRun: llm.dryRun },
    });

    try {
      if (repeatingPattern(events.all())) {
        await tools.execute(employee.id, "journal", {
          text: "Caught a loop. Changing the subject: what is the smallest next change to Stamp?",
        });
        await events.append({
          type: "loop_broken",
          actor: employee.id,
          message: `${employee.name} broke a repeating loop`,
        });
      }

      const constitution = await readDoc("constitution.md");
      const strategy = await readDoc("strategy.md");
      const { messages } = await assembleContext({
        workspaceRoot,
        employee,
        employees,
        events,
        constitution,
        strategy,
      });
      const result = await llm.complete({ employee, messages });
      await budget.recordSpend(result.costUsd || 0);

      const calls = result.toolCalls?.length
        ? result.toolCalls
        : fallbackCalls(employee, result.text);

      const outcomes = [];
      for (const call of calls.slice(0, 4)) {
        outcomes.push(await tools.execute(employee.id, call.name, call.arguments));
      }

      await events.append({
        type: "turn_finished",
        actor: employee.id,
        message: `${employee.name} finished a turn${result.dryRun ? " (dry-run)" : ""}`,
        data: {
          model: result.model,
          dryRun: Boolean(result.dryRun),
          costUsd: result.costUsd || 0,
          tools: calls.map((call) => call.name),
        },
      });
      return { employee, outcomes, result };
    } catch (error) {
      await events.append({
        type: "turn_failed",
        actor: employee.id,
        message: `${employee.name} turn failed: ${error.message}`,
        data: { error: error.message },
      });
      return { error: error.message, employee };
    } finally {
      running = false;
    }
  }

  function schedule() {
    if (stopped) return;
    const delay = nextTickDelay(tick.minMs, tick.maxMs, random);
    if (onTickScheduled) onTickScheduled(delay);
    timer = setTimeout(() => {
      tickOnce()
        .catch(() => {})
        .finally(schedule);
    }, delay);
    timer.unref?.();
  }

  return {
    tickOnce,
    start() {
      stopped = false;
      schedule();
    },
    stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}

function fallbackCalls(employee, text) {
  const message = filterSay(text);
  if (message) return [{ name: "say", arguments: { message } }];
  return [
    {
      name: "journal",
      arguments: { text: `${employee.name} sat with the problem and did not speak.` },
    },
  ];
}

function nextUtcDay(ts) {
  const date = new Date(ts);
  date.setUTCDate(date.getUTCDate() + 1);
  date.setUTCHours(0, 0, 0, 0);
  return date.toISOString().slice(0, 10);
}
