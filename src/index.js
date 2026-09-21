import { createStudio } from "./studio.js";

const studio = await createStudio({ root: process.cwd() });
await studio.start();

const addr = studio.server.server.address();
const host = typeof addr === "object" && addr ? addr.address : studio.config.host;
const port = typeof addr === "object" && addr ? addr.port : studio.config.port;

console.log(`Fishbowl listening on http://${host}:${port}`);
console.log(studio.llm.dryRun ? "Mode: dry-run (no OPENROUTER_API_KEY)" : "Mode: OpenRouter live");
console.log(`Daily ceiling: $${studio.config.budget.dailyCeilingUsd.toFixed(2)}`);
console.log(`Kill switch: http://${host}:${port}/kill`);
console.log(
  `Tick: ${studio.config.tick.minMs / 1000}–${studio.config.tick.maxMs / 1000}s · one employee at a time`,
);

const shutdown = async (signal) => {
  console.log(`\n${signal} — pausing the clock`);
  await studio.stop();
  process.exit(0);
};

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
