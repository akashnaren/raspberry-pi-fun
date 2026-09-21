import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export function utcDay(ts) {
  return new Date(ts).toISOString().slice(0, 10);
}

export async function createBudget({
  filePath,
  dailyCeilingUsd = 5,
  now = () => Date.now(),
}) {
  await mkdir(dirname(filePath), { recursive: true });
  let state = await load(filePath, now);

  function snapshot() {
    rollDay();
    const ceiling = Number(dailyCeilingUsd);
    const spent = Number(state.spentUsd) || 0;
    return {
      day: state.day,
      spentUsd: roundUsd(spent),
      ceilingUsd: ceiling,
      remainingUsd: roundUsd(Math.max(0, ceiling - spent)),
      turns: state.turns,
      exhausted: spent >= ceiling,
    };
  }

  function rollDay() {
    const today = utcDay(now());
    if (state.day !== today) {
      state = { day: today, spentUsd: 0, turns: 0 };
    }
  }

  async function save() {
    await writeFile(filePath, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  }

  return {
    snapshot,
    isExhausted() {
      return snapshot().exhausted;
    },
    async recordSpend(usd) {
      rollDay();
      const amount = Number(usd);
      if (!Number.isFinite(amount) || amount <= 0) {
        state.turns += 1;
        await save();
        return snapshot();
      }
      state.spentUsd = roundUsd((Number(state.spentUsd) || 0) + amount);
      state.turns += 1;
      await save();
      return snapshot();
    },
  };
}

function roundUsd(n) {
  return Math.round(n * 1e6) / 1e6;
}

async function load(filePath, now) {
  try {
    const parsed = JSON.parse(await readFile(filePath, "utf8"));
    return {
      day: parsed.day || utcDay(now()),
      spentUsd: Number(parsed.spentUsd) || 0,
      turns: Number(parsed.turns) || 0,
    };
  } catch (error) {
    if (error.code === "ENOENT") {
      return { day: utcDay(now()), spentUsd: 0, turns: 0 };
    }
    throw error;
  }
}
