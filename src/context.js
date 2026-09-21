import { readFile } from "node:fs/promises";
import { resolveWorkspacePath } from "./paths.js";
import { opinionsFor } from "./relationships.js";
import { displayName } from "./headline.js";

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

export function cachedConstitutionPrefix(constitution) {
  return [
    "Meridian Desk constitution (static prefix — cache this).",
    constitution.trim() || "(none)",
  ].join("\n");
}

export async function assembleContext({ workspaceRoot, employee, employees, events, constitution, strategy }) {
  const persona = await readOptional(workspaceRoot, `employees/${employee.id}/persona.md`);
  const board = await readOptional(workspaceRoot, "board/inbox.md");
  let backlogRaw = await readOptional(workspaceRoot, "backlog.json");
  let backlog = { tasks: [] };
  try {
    backlog = JSON.parse(backlogRaw || '{"tasks":[]}');
  } catch {
    backlog = { tasks: [] };
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

  let relationships = { pairs: [] };
  try {
    relationships = JSON.parse((await readOptional(workspaceRoot, "relationships.json")) || "{}");
  } catch {
    relationships = { pairs: [] };
  }
  const opinions = opinionsFor(relationships, employee.id);
  const names = Object.fromEntries((employees || []).map((person) => [person.id, person.name]));
  const opinionLines = opinions.length
    ? opinions
        .map((item) => `${displayName(item.other, names)}: ${item.score > 0 ? "+" : ""}${item.score}${item.note ? ` — ${item.note}` : ""}`)
        .join("\n")
    : "(neutral)";

  const cached = cachedConstitutionPrefix(constitution);
  const variable = [
    `Role: ${employee.name}, ${employee.role} at Meridian Desk.`,
    "You take exactly one turn. Call tools. Do not write secrets. Do not touch machinery (src/, public/, config, env).",
    "The product panel shows dist/ — the last green build — never the working copy.",
    "Flagship is Meridian Office. Docs first. Sheets and Slides stay stubs. Timezone Buddy stays in the catalogue. No accounts, payments, uploads, or chat.",
    "Speech is at most two short lines, in your own voice.",
    "You may edit_self_aesthetics for your own look. Only Jules Park may edit_office. Ask Jules via request() for furniture.",
    "Treat people as reasonable professionals with conflicting priorities. Do not perform conflict.",
    `Your priorities: ${employee.priorities}`,
    "",
    "# Persona",
    persona.trim() || "(none)",
    "",
    "# Strategy",
    strategy.trim() || "(none)",
    "",
    "# Board inbox",
    board.trim() || "(empty)",
    "",
    "# Open backlog",
    JSON.stringify(openTasks(backlog), null, 2),
    "",
    "# How you feel about people in the room (−2..+2, decays toward 0)",
    opinionLines,
    "",
    "# Last ten events (the office is a rendering of these)",
    recent.map((event) => `${event.id} ${event.actor} ${event.type}: ${event.headline || event.message}`).join("\n") ||
      "(none yet)",
    "",
    focusPath ? `# File in hand: ${focusPath}` : "# No file in hand",
    focus || "",
  ].join("\n");

  return {
    messages: [
      {
        role: "system",
        content: [{ type: "text", text: cached, cache_control: { type: "ephemeral" } }],
      },
      { role: "user", content: variable },
    ],
    cachedPrefix: cached,
    focusPath,
  };
}

export function inferFocusFile(employee, recent) {
  if (employee.role === "programmer") return "product/index.html";
  if (employee.role === "qa") return "product/index.html";
  if (employee.role === "producer") return "backlog.json";
  if (employee.role === "office_manager") return "office.json";
  const written = [...recent].reverse().find((event) => event.type === "file_written");
  return written?.data?.path || null;
}

export function repeatingPattern(events, windowSize = 8) {
  if (events.length < windowSize) return false;
  const slice = events.slice(-windowSize);
  const actionable = slice.filter((event) => ["say", "journal", "task_added"].includes(event.type));
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
