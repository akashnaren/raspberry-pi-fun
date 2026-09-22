import { loadEnvironmentFiles } from "./env-file.js";
import { meshNetworkAllowed } from "./mesh.js";
import { createStudio } from "./studio.js";

loadEnvironmentFiles({ root: process.cwd() });

const studio = await createStudio({ root: process.cwd() });
await studio.start();

const addr = studio.server.server.address();
const host = typeof addr === "object" && addr ? addr.address : studio.config.host;
const port = typeof addr === "object" && addr ? addr.port : studio.config.port;

console.log(`Office listening on http://${host}:${port}`);
if (host === "0.0.0.0" || host === "::") {
  console.log(`This machine: http://127.0.0.1:${port}`);
}
const modeLabel = studio.config.mode.replay
  ? "Mode: replay (event log only, zero tokens)"
  : studio.llm.dryRun
    ? "Mode: dry-run (DRY_RUN default or no OPENROUTER_API_KEY)"
    : "Mode: OpenRouter live";
console.log(modeLabel);
const mesh = studio.config.mesh;
if (!mesh?.enabled) {
  console.log("Mesh: off (MESH_URL unset — dry-run / OpenRouter path unchanged)");
} else if (meshNetworkAllowed(process.env)) {
  console.log(`Mesh: ${mesh.url} target ${mesh.target} (${mesh.kind}) — local lines, not OpenRouter`);
} else {
  console.log(`Mesh: ${mesh.url} target ${mesh.target} (${mesh.kind}) — DRY_RUN skips network`);
}
console.log(`Daily ceiling: $${studio.config.budget.dailyCeilingUsd.toFixed(2)}`);
console.log(`Kill switch: http://${host}:${port}/kill`);
console.log(
  `Tick: ${studio.config.tick.minMs / 1000}–${studio.config.tick.maxMs / 1000}s (mid ${studio.config.tick.midMs / 1000}s) · one employee at a time`,
);

const shutdown = async (signal) => {
  console.log(`\n${signal} — pausing the clock`);
  await studio.stop();
  process.exit(0);
};

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
