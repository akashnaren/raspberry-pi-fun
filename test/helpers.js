import { cp, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const REPO = process.cwd();

export async function tempStudioRoot() {
  const dir = await mkdtemp(join(tmpdir(), "fishbowl-"));
  await cp(join(REPO, "studio.config.json"), join(dir, "studio.config.json"));
  await cp(join(REPO, "public"), join(dir, "public"), { recursive: true });
  await cp(join(REPO, "workspace"), join(dir, "workspace"), { recursive: true });
  return dir;
}

export function testEnv(overrides = {}) {
  return {
    OPENROUTER_API_KEY: "",
    DAILY_CEILING_USD: "5",
    PORT: "0",
    HOST: "127.0.0.1",
    STUDIO_TICK_MIN_MS: "60000",
    STUDIO_TICK_MAX_MS: "60000",
    ...overrides,
  };
}
