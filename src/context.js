import { readFile } from "node:fs/promises";
import { resolveWorkspacePath } from "./paths.js";

const FILE_CAP = 400;

async function readOptional(workspaceRoot, rel) {
  try {
    const { abs } = resolveWorkspacePath(workspaceRoot, rel);
    return await readFile(abs, "utf8");
  } catch {
    return "";
  }
}

export function openTasks(backlog) {
  return (backlog?.tasks || []).filter((task) => task.status === "open");
}

export async function assembleContext({ workspaceRoot, employee, employees, events, constitution, strategy }) {
  const persona = await readOptional(workspaceRoot, `employees/${employee.id}/persona.md`);
  const journal = await readOptional(workspaceRoot, `employees/${employee.id}/journal.md`);
  const studio = await readOptional(workspaceRoot, "STUDIO.md");
  let backlogRaw = await readOptional(workspaceRoot, "backlog.json");
  let backlog = { tasks: [] };
  try {
    backlog = JSON.parse(backlogRaw || '{"tasks":[]}');
  } catch {
    backlogRaw = '{"tasks":[]}';
  }
  const recent = events.recent(10);
  const focusPath = inferFocusFile(employee, recent);
  let focus = "";
  if (focusPath) {
    focus = await readOptional(workspaceRoot, focusPath);
    const lines = focus.split("\n");
    if (lines.length > FILE_CAP) {
      focus = `${lines.slice(0, FILE_CAP).join("\n")}\n\n/* truncated after ${FILE_CAP} lines — rewrite the whole file if you edit it */\n`;
    }
  }

  const roster = employees
    .map((person) => `${person.name} (${person.id}) — ${person.role} — model ${person.model || person.modelFamily}. ${person.priorities}`)
    .join("\n");

  const system = [
    `You are ${employee.name}, ${employee.role} at Fishbowl, a private three-person AI studio.`,
    "You take exactly one turn. Call tools. Do not write secrets. Do not touch machinery (src/, public/, config, env).",
    "The product panel shows dist/ — the last green build — never the working copy.",
    "Keep Stamp a single-file browser timestamp tool. No accounts, payments, uploads, or chat boxes.",
    "For files under ~400 lines, rewrite the whole file rather than a patch.",
    "",
    persona.trim(),
    "",
    `Your priorities: ${employee.priorities}`,
    "",
    "Colleagues (relationship scores arrive in Stage 2; treat them as reasonable professionals with conflicting priorities):",
    roster,
  ].join("\n");

  const user = [
    "# Constitution",
    constitution.trim() || "(none)",
    "",
    "# Strategy",
    strategy.trim() || "(none)",
    "",
    "# STUDIO.md",
    studio.trim() || "(none)",
    "",
    "# Open backlog",
    JSON.stringify(openTasks(backlog), null, 2),
    "",
    "# Last ten events (the office is a rendering of these)",
    recent.map((event) => `${event.id} ${event.actor} ${event.type}: ${event.message}`).join("\n") || "(none yet)",
    "",
    `# Journal excerpt`,
    journal.trim().split("\n").slice(-20).join("\n") || "(empty)",
    "",
    focusPath ? `# File in hand: ${focusPath}` : "# No file in hand",
    focus || "",
  ].join("\n");

  return {
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
    focusPath,
  };
}

export function inferFocusFile(employee, recent) {
  if (employee.role === "programmer") return "product/index.html";
  if (employee.role === "qa") return "product/index.html";
  if (employee.role === "producer") return "backlog.json";
  const written = [...recent].reverse().find((event) => event.type === "file_written");
  return written?.data?.path || null;
}

export function repeatingPattern(events, windowSize = 8) {
  if (events.length < windowSize) return false;
  const slice = events.slice(-windowSize);
  const actionable = slice.filter((event) =>
    ["say", "journal", "task_added"].includes(event.type),
  );
  if (actionable.length < 6) return false;
  const keys = actionable.map((event) => `${event.actor}:${event.type}:${normalize(event.message)}`);
  const unique = new Set(keys);
  return unique.size <= 2;
}

function normalize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/dry-run turn \d+/g, "")
    .replace(/\d+/g, "#")
    .replace(/\s+/g, " ")
    .trim();
}
