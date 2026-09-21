import { readFile } from "node:fs/promises";
import { assembleContext, repeatingPattern } from "./context.js";
import { resolveWorkspacePath } from "./paths.js";
import { stubCall } from "./stubs.js";
import { filterSay } from "./tools.js";

export function weightOf(employee, lottery) {
  const raw = lottery?.[employee.id] ?? employee.lottery_weight ?? employee.lotteryWeight ?? 1;
  const n = Number(raw);
  if (lottery && Object.hasOwn(lottery, employee.id) && n === 0) return 0;
  return Number.isFinite(n) && n > 0 ? n : 1;
}

export function pickEmployee(employees, lottery, random) {
  const weights = employees.map((employee) => ({
    employee,
    weight: weightOf(employee, lottery),
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
  replay = false,
  localStubs = true,
  now = () => Date.now(),
  random = Math.random,
  onTickScheduled,
}) {
  let timer = null;
  let stopped = false;
  let running = false;
  let replayIndex = 0;

  async function readDoc(rel) {
    try {
      const { abs } = resolveWorkspacePath(workspaceRoot, rel);
      return await readFile(abs, "utf8");
    } catch {
      return "";
    }
  }

  function alreadySleepingToday(day) {
    return events
      .all()
      .some((event) => event.type === "budget_paused" && event.data?.day === day);
  }

  async function replayOnce() {
    const replayable = events.all().filter((event) => event.actor && event.actor !== "system");
    if (!replayable.length) return { skipped: "replay-empty" };
    const event = replayable[replayIndex % replayable.length];
    replayIndex += 1;
    return { replayed: event, skipped: "replay" };
  }

  async function tickOnce() {
    if (running) return { skipped: "in-flight" };
    if (replay) return replayOnce();
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
      if (localStubs && !alreadySleepingToday(snap.day)) {
        const employee = pickEmployee(employees, lottery, random);
        const stub = stubCall(employee, "say");
        await tools.execute(employee.id, stub.name, stub.arguments);
      }
      if (!alreadySleepingToday(snap.day)) {
        await events.append({
          type: "budget_paused",
          actor: "system",
          message: "studio sleeping",
          data: { ...snap, sleeping: true },
        });
      }
      return { skipped: "budget" };
    }

    running = true;
    const employee = pickEmployee(employees, lottery, random);
    const kind = employee.role === "programmer" ? "write" : "chatter";
    await events.append({
      type: "turn_started",
      actor: employee.id,
      message: `${employee.name} takes a turn`,
      data: { role: employee.role, model: employee.model, dryRun: llm.dryRun, kind },
    });

    try {
      if (repeatingPattern(events.all())) {
        await tools.execute(employee.id, "journal", {
          text: "Caught a loop. Changing the subject: what is the smallest next change to Docs?",
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
      const result = await llm.complete({ employee, messages, kind });
      await budget.recordSpend(result.costUsd || 0);

      const calls = result.toolCalls?.length ? result.toolCalls : fallbackCalls(employee, result.text);

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
          kind,
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
