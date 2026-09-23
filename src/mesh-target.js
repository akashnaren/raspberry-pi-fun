/**
 * Runtime mesh brain. data/MESH_TARGET layers over env MESH_TARGET / MESH_KIND.
 * It does not rewrite .env. The next job re-reads the file.
 */

import { readFileSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { meshSettingsFromEnv, normalizeMeshTarget } from "./mesh.js";

export const MESH_TARGET_FILENAME = "MESH_TARGET";

export function meshTargetPath(dataRoot) {
  return join(dataRoot, MESH_TARGET_FILENAME);
}

export function readMeshTargetOverride(filePath) {
  try {
    const parsed = JSON.parse(readFileSync(filePath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed;
  } catch (error) {
    if (error.code === "ENOENT" || error instanceof SyntaxError) return null;
    throw error;
  }
}

export function applyMeshTargetOverride(settings, override) {
  if (!override) return settings;
  const next = { ...settings };
  if (override.kind === "ollama" || override.kind === "pair") next.kind = override.kind;
  if (override.target != null && String(override.target).trim()) {
    try {
      next.target = normalizeMeshTarget(override.target);
    } catch {
      // A torn or unknown pin leaves the env target in place.
    }
  }
  return next;
}

export function effectiveMeshSettings(env, filePath) {
  return applyMeshTargetOverride(meshSettingsFromEnv(env), readMeshTargetOverride(filePath));
}

async function writeOverride(filePath, record) {
  await mkdir(dirname(filePath), { recursive: true });
  await writeFile(filePath, `${JSON.stringify(record, null, 2)}\n`, "utf8");
}

export function createMeshTarget({ dataRoot, filePath, env = process.env } = {}) {
  const path = filePath || meshTargetPath(dataRoot);

  function settings() {
    return effectiveMeshSettings(env, path);
  }

  async function setPairTarget(target, source = "api") {
    const normalized = normalizeMeshTarget(target);
    await writeOverride(path, {
      target: normalized,
      kind: "pair",
      source,
      ts: Date.now(),
    });
    return settings();
  }

  async function setOllama(source = "api") {
    await writeOverride(path, {
      target: "auto",
      kind: "ollama",
      source,
      ts: Date.now(),
    });
    return settings();
  }

  return {
    filePath: path,
    settings,
    setPairTarget,
    setOllama,
  };
}
