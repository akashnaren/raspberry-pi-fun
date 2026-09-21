import { readFile } from "node:fs/promises";
import { isProductPath, resolveWorkspacePath, assertWritableFile } from "./paths.js";
import { emptyBacklog, validateBacklog, validateOffice } from "./schemas.js";
import { syntaxCheckFile, writeFileAtomic } from "./validator.js";

const SAY_MAX = 240;

export const TOOL_SCHEMAS = [
  {
    type: "function",
    function: {
      name: "read_file",
      description: "Read a file from the studio workspace (data only, never machinery).",
      parameters: {
        type: "object",
        properties: { path: { type: "string" } },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description:
        "Rewrite a workspace file in full. Use for product HTML, backlog, office.json, strategy, journals. Never write machinery or secrets.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string" },
          contents: { type: "string" },
        },
        required: ["path", "contents"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "add_task",
      description: "Append an open task to the shared backlog.",
      parameters: {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "close_task",
      description: "Close an open backlog task by id.",
      parameters: {
        type: "object",
        properties: { id: { type: "string" } },
        required: ["id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "say",
      description: "Speak in the office. Becomes a speech bubble and an event.",
      parameters: {
        type: "object",
        properties: { message: { type: "string" } },
        required: ["message"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "journal",
      description: "Write a private-ish note others can still read later.",
      parameters: {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"],
      },
    },
  },
];

export function filterSay(message) {
  const cleaned = String(message ?? "")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "")
    .replace(/<[^>]+>/g, "")
    .trim();
  if (!cleaned) return "";
  return cleaned.length > SAY_MAX ? `${cleaned.slice(0, SAY_MAX - 3)}...` : cleaned;
}

export function createToolRunner({
  workspaceRoot,
  events,
  now = () => Date.now(),
  onProductWrite,
}) {
  const backlogPath = "backlog.json";

  async function loadBacklog() {
    const { abs } = resolveWorkspacePath(workspaceRoot, backlogPath);
    try {
      const parsed = JSON.parse(await readFile(abs, "utf8"));
      const error = validateBacklog(parsed);
      if (error) return emptyBacklog();
      return parsed;
    } catch {
      return emptyBacklog();
    }
  }

  async function saveBacklog(backlog) {
    const { abs } = resolveWorkspacePath(workspaceRoot, backlogPath);
    await writeFileAtomic(abs, `${JSON.stringify(backlog, null, 2)}\n`);
  }

  async function nextTaskId(backlog) {
    const nums = backlog.tasks
      .map((task) => Number(/^t-(\d+)$/.exec(task.id)?.[1]))
      .filter((n) => Number.isFinite(n));
    const next = (nums.length ? Math.max(...nums) : 0) + 1;
    return `t-${next}`;
  }

  return {
    async execute(actor, name, rawArgs) {
      const args = rawArgs && typeof rawArgs === "object" ? rawArgs : {};
      try {
        if (name === "read_file") return await readTool(actor, args);
        if (name === "write_file") return await writeTool(actor, args);
        if (name === "add_task") return await addTask(actor, args);
        if (name === "close_task") return await closeTask(actor, args);
        if (name === "say") return await sayTool(actor, args);
        if (name === "journal") return await journalTool(actor, args);
        throw new Error(`unknown tool ${name}`);
      } catch (error) {
        const event = await events.append({
          type: "tool_error",
          actor,
          message: `${actor} failed ${name}: ${error.message}`,
          data: { tool: name, error: error.message },
        });
        return { ok: false, error: error.message, event };
      }
    },
  };

  async function readTool(actor, args) {
    const { abs, rel } = resolveWorkspacePath(workspaceRoot, args.path);
    const contents = await readFile(abs, "utf8");
    const event = await events.append({
      type: "file_read",
      actor,
      message: `${actor} read ${rel}`,
      data: { path: rel, bytes: contents.length },
    });
    return { ok: true, path: rel, contents, event };
  }

  async function writeTool(actor, args) {
    const { abs, rel } = resolveWorkspacePath(workspaceRoot, args.path);
    assertWritableFile(rel);
    const contents = String(args.contents ?? "");
    if (contents.length > 200_000) throw new Error("file too large");

    if (rel === "office.json") {
      let parsed;
      try {
        parsed = JSON.parse(contents);
      } catch (error) {
        throw new Error(`office.json is not JSON: ${error.message}`);
      }
      const error = validateOffice(parsed);
      if (error) throw new Error(error);
    }
    if (rel === "backlog.json") {
      let parsed;
      try {
        parsed = JSON.parse(contents);
      } catch (error) {
        throw new Error(`backlog.json is not JSON: ${error.message}`);
      }
      const error = validateBacklog(parsed);
      if (error) throw new Error(error);
    }

    const product = isProductPath(rel);
    if (!product && (rel.endsWith(".json") || rel.endsWith(".js"))) {
      const syntax = syntaxCheckFile(rel, contents);
      if (!syntax.ok) throw new Error(syntax.error);
    }

    await writeFileAtomic(abs, contents);
    const event = await events.append({
      type: "file_written",
      actor,
      message: `${actor} wrote ${rel}`,
      data: { path: rel, bytes: contents.length, product },
    });

    let build = null;
    if (product) {
      const writtenCheck = syntaxCheckFile(rel, contents);
      if (!writtenCheck.ok) {
        const fail = await events.append({
          type: "build_failed",
          actor,
          message: `build failed syntax check on ${rel}: ${writtenCheck.error}`,
          data: { path: rel, error: writtenCheck.error, stage: "syntax" },
        });
        return { ok: true, path: rel, event, build: "failed", error: writtenCheck.error, fail };
      }
      if (onProductWrite) {
        build = await onProductWrite({ actor, path: rel, contents });
      }
    }
    return { ok: true, path: rel, event, build };
  }

  async function addTask(actor, args) {
    const text = String(args.text ?? "").trim();
    if (!text) throw new Error("task text is required");
    const backlog = await loadBacklog();
    const task = {
      id: await nextTaskId(backlog),
      text,
      status: "open",
      createdBy: actor,
      createdAt: now(),
    };
    backlog.tasks.push(task);
    await saveBacklog(backlog);
    const event = await events.append({
      type: "task_added",
      actor,
      message: `${actor} added task ${task.id}: ${text}`,
      data: { task },
    });
    return { ok: true, task, event };
  }

  async function closeTask(actor, args) {
    const id = String(args.id ?? "").trim();
    const backlog = await loadBacklog();
    const task = backlog.tasks.find((item) => item.id === id);
    if (!task) throw new Error(`unknown task ${id}`);
    task.status = "closed";
    task.closedBy = actor;
    task.closedAt = now();
    await saveBacklog(backlog);
    const event = await events.append({
      type: "task_closed",
      actor,
      message: `${actor} closed task ${id}`,
      data: { task },
    });
    return { ok: true, task, event };
  }

  async function sayTool(actor, args) {
    const message = filterSay(args.message);
    if (!message) throw new Error("empty say");
    const event = await events.append({
      type: "say",
      actor,
      message: `${actor}: ${message}`,
      data: { text: message },
    });
    return { ok: true, text: message, event };
  }

  async function journalTool(actor, args) {
    const text = String(args.text ?? "").trim();
    if (!text) throw new Error("journal text is required");
    const rel = `employees/${actor}/journal.md`;
    const { abs } = resolveWorkspacePath(workspaceRoot, rel);
    let existing = "";
    try {
      existing = await readFile(abs, "utf8");
    } catch {
      existing = `# ${actor} journal\n`;
    }
    const stamp = new Date(now()).toISOString();
    const next = `${existing.trimEnd()}\n\n## ${stamp}\n\n${text}\n`;
    await writeFileAtomic(abs, next);
    const event = await events.append({
      type: "journal",
      actor,
      message: `${actor} wrote in their journal`,
      data: { path: rel, excerpt: text.slice(0, 160) },
    });
    return { ok: true, path: rel, event };
  }
}
