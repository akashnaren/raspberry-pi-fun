export function resolveStudioMode(env = process.env) {
  const replay = env.STUDIO_MODE === "replay" || env.REPLAY === "true" || env.REPLAY === "1";
  const flag = env.DRY_RUN;
  const explicitlyLive = flag === "false" || flag === "0";
  const hasKey = Boolean(env.OPENROUTER_API_KEY);
  const dryRun = replay || !explicitlyLive || !hasKey;
  const localStubs = env.STUDIO_LOCAL_STUBS !== "false" && env.STUDIO_LOCAL_STUBS !== "0";
  const lite = env.STUDIO_LITE === "true" || env.STUDIO_LITE === "1";
  return {
    dryRun,
    replay,
    localStubs,
    lite,
    live: !dryRun && !replay,
  };
}

export function runwayHours({ spentUsd, remainingUsd, now = Date.now() }) {
  const spent = Number(spentUsd) || 0;
  const remaining = Number(remainingUsd) || 0;
  if (spent <= 0) return null;
  const start = Date.UTC(
    new Date(now).getUTCFullYear(),
    new Date(now).getUTCMonth(),
    new Date(now).getUTCDate(),
  );
  const hours = Math.max(1 / 60, (now - start) / 3_600_000);
  const rate = spent / hours;
  if (rate <= 0) return null;
  return Math.round((remaining / rate) * 10) / 10;
}
