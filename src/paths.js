import { resolve, relative, sep } from "node:path";

const BLOCKED_PREFIXES = [
  "src/",
  "public/",
  "test/",
  "deploy/",
  "data/",
  "dist/",
  "node_modules/",
];

const BLOCKED_FILES = new Set([
  "package.json",
  "package-lock.json",
  "studio.config.json",
  ".env",
  ".env.example",
  ".gitignore",
]);

const ALLOWED_WRITE_EXT = new Set([
  ".md",
  ".json",
  ".html",
  ".htm",
  ".css",
  ".js",
  ".txt",
]);

export function isMachineryRel(rel) {
  const normalized = rel.replaceAll("\\", "/").replace(/^\.?\//, "");
  if (BLOCKED_FILES.has(normalized)) return true;
  if (normalized.startsWith(".")) return true;
  return BLOCKED_PREFIXES.some(
    (prefix) => normalized === prefix.slice(0, -1) || normalized.startsWith(prefix),
  );
}

export function resolveWorkspacePath(workspaceRoot, userPath) {
  if (userPath == null || String(userPath).trim() === "") {
    throw new Error("path is required");
  }
  const raw = String(userPath).trim().replaceAll("\\", "/");
  if (raw.startsWith("/") || /^[a-zA-Z]:/.test(raw)) {
    throw new Error("absolute paths are not allowed");
  }
  const abs = resolve(workspaceRoot, raw);
  const relToWorkspace = relative(workspaceRoot, abs);
  if (relToWorkspace.startsWith("..") || relToWorkspace === "") {
    throw new Error("path escapes the workspace");
  }
  const workspaceRel = relToWorkspace.split(sep).join("/");
  if (isMachineryRel(workspaceRel)) {
    throw new Error("bots edit data, never machinery");
  }
  return { abs, rel: workspaceRel };
}

export function assertWritableFile(rel) {
  const lower = rel.toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot >= 0 ? lower.slice(dot) : "";
  if (!ALLOWED_WRITE_EXT.has(ext)) {
    throw new Error(`refusing to write ${ext || "extensionless"} files`);
  }
}

export function isProductPath(rel) {
  return rel === "product" || rel.startsWith("product/");
}
