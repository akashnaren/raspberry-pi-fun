import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const DEFAULT_SECRET_REL = join(".secrets", "fishbowl", "openrouter.env");

export function parseEnvironmentFile(text) {
  const out = {};
  if (text == null) return out;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
      (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
    ) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export function environmentFilePaths({
  root = process.cwd(),
  home = process.env.HOME || homedir(),
} = {}) {
  return [join(root, ".env"), join(home, DEFAULT_SECRET_REL)];
}

/**
 * systemd EnvironmentFile=-path: missing files never block start.
 * Later files win over earlier files. Already-set non-empty process env wins.
 */
export function loadEnvironmentFiles({
  root = process.cwd(),
  home = process.env.HOME || homedir(),
  env = process.env,
  paths,
} = {}) {
  const files = paths || environmentFilePaths({ root, home });
  const fromFiles = {};
  const loaded = [];
  for (const file of files) {
    if (!file || !existsSync(file)) continue;
    try {
      Object.assign(fromFiles, parseEnvironmentFile(readFileSync(file, "utf8")));
      loaded.push(file);
    } catch {
      // unreadable files are treated like the systemd "-" prefix
    }
  }
  for (const [key, value] of Object.entries(fromFiles)) {
    const current = env[key];
    if (current !== undefined && current !== "") continue;
    env[key] = value;
  }
  if (env.DRY_RUN === undefined || env.DRY_RUN === "") {
    env.DRY_RUN = "true";
  }
  return loaded;
}
