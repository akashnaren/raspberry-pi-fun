import { appendFile, readFile } from "node:fs/promises";
import { applyAestheticsPatch, loadEmployeeRecord, validateEmployeeRecord } from "./aesthetics.js";
import { isProductPath, resolveWorkspacePath, assertWritableFile } from "./paths.js";
import { applyOfficeTweak, emptyBacklog, isOfficeTweak, validateBacklog, validateOffice } from "./schemas.js";
import { validateRelationships } from "./relationships.js";
import { syntaxCheckFile, writeFileAtomic } from "./validator.js";

const SAY_MAX = 84;

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
        "Rewrite a workspace file in full. Product, backlog, strategy, journals. office.json is Jules only. employees/<id>.json aesthetics are self only.",
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
  {
    type: "function",
    function: {
      name: "request",
      description: "Ask the office manager for furniture or a wardrobe unlock.",
      parameters: {
        type: "object",
        properties: {
          item: { type: "string" },
          reason: { type: "string" },
        },
        required: ["item", "reason"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "edit_office",
      description: "Office manager only. Patch office.json (desks, decor, rooms). Spends furniture budget on new props.",
      parameters: {
        type: "object",
        properties: {
          office: { type: "object" },
        },
        required: ["office"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "edit_self_aesthetics",
      description: "Change your own clothes and look. Only pieces in wardrobe_unlocked.",
      parameters: {
        type: "object",
        properties: {
          skin: { type: "string" },
          hair: { type: "string" },
          desk_style: { type: "string" },
          outfit: {
            type: "object",
            properties: {
              top: { type: "string" },
              bottom: { type: "string" },
              shoes: { type: "string" },
              accessory: { type: "string" },
            },
          },
        },
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
  employees = [],
}) {
  const byId = new Map(employees.map((person) => [person.id, person]));

  function isOfficeManager(actor) {
    return byId.get(actor)?.role === "office_manager" || actor === "jules";
  }

  const backlogPath = "backlog.json";

  async function loadJson(rel, fallback) {
    const { abs } = resolveWorkspacePath(workspaceRoot, rel);
    try {
      return JSON.parse(await readFile(abs, "utf8"));
    } catch {
      return fallback;
    }
  }

  async function saveJson(rel, value) {
    const { abs } = resolveWorkspacePath(workspaceRoot, rel);
    await writeFileAtomic(abs, `${JSON.stringify(value, null, 2)}\n`);
  }

  async function loadBacklog() {
    const parsed = await loadJson(backlogPath, emptyBacklog());
    return validateBacklog(parsed) ? emptyBacklog() : parsed;
  }

  async function nextTaskId(backlog) {
    const nums = backlog.tasks
      .map((task) => Number(/^t-(\d+)$/.exec(task.id)?.[1]))
      .filter((n) => Number.isFinite(n));
    return `t-${(nums.length ? Math.max(...nums) : 0) + 1}`;
  }

  async function nextRequestId(list) {
    const nums = list
      .map((item) => Number(/^r-(\d+)$/.exec(item.id)?.[1]))
      .filter((n) => Number.isFinite(n));
    return `r-${(nums.length ? Math.max(...nums) : 0) + 1}`;
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
        if (name === "request") return await requestTool(actor, args);
        if (name === "edit_office") return await editOffice(actor, args);
        if (name === "edit_self_aesthetics") return await editSelf(actor, args);
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

    const selfRecord = rel.match(/^employees\/([^/]+)\.json$/);
    if (selfRecord) {
      if (selfRecord[1] !== actor) throw new Error("only you can change your own look");
      let parsed;
      try {
        parsed = JSON.parse(contents);
      } catch (error) {
        throw new Error(`employee record is not JSON: ${error.message}`);
      }
      const existing = (await loadEmployeeRecord(workspaceRoot, actor)) || parsed;
      const merged = {
        ...existing,
        aesthetics: parsed.aesthetics || existing.aesthetics,
        wardrobe_unlocked: existing.wardrobe_unlocked,
        id: actor,
        name: existing.name,
        role: existing.role,
        accent: existing.accent,
      };
      const error = validateEmployeeRecord(merged, actor);
      if (error) throw new Error(error);
      await writeFileAtomic(abs, `${JSON.stringify(merged, null, 2)}\n`);
      const event = await events.append({
        type: "aesthetics_changed",
        actor,
        data: { aesthetics: merged.aesthetics },
      });
      return { ok: true, path: rel, event };
    }

    if (rel === "office.json") {
      if (!isOfficeManager(actor)) throw new Error("only the office manager can edit office.json");
      return editOffice(actor, { office: JSON.parse(contents) });
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
    if (rel === "relationships.json") {
      let parsed;
      try {
        parsed = JSON.parse(contents);
      } catch (error) {
        throw new Error(`relationships.json is not JSON: ${error.message}`);
      }
      const error = validateRelationships(parsed);
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
    await saveJson(backlogPath, backlog);
    const event = await events.append({
      type: "task_added",
      actor,
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
    await saveJson(backlogPath, backlog);
    const event = await events.append({
      type: "task_closed",
      actor,
      data: { task },
    });
    return { ok: true, task, event };
  }

  async function sayTool(actor, args) {
    const message = filterSay(args.message);
    if (!message) throw new Error("empty say");
    const to = String(args.to || "").toLowerCase();
    const known = new Set(["nova", "kessler", "mira", "jules"]);
    const event = await events.append({
      type: "say",
      actor,
      data: {
        text: message,
        ...(known.has(to) && to !== actor ? { to } : {}),
      },
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
    await writeFileAtomic(abs, `${existing.trimEnd()}\n\n## ${stamp}\n\n${text}\n`);
    const event = await events.append({
      type: "journal",
      actor,
      data: { path: rel, excerpt: text.slice(0, 160) },
    });
    return { ok: true, path: rel, event };
  }

  async function requestTool(actor, args) {
    const item = String(args.item ?? "").trim();
    const reason = String(args.reason ?? "").trim();
    if (!item || !reason) throw new Error("item and reason are required");
    const pile = await loadJson("requests.json", { requests: [] });
    if (!Array.isArray(pile.requests)) pile.requests = [];
    const entry = {
      id: await nextRequestId(pile.requests),
      item,
      reason,
      from: actor,
      status: "open",
      createdAt: now(),
    };
    pile.requests.push(entry);
    await saveJson("requests.json", pile);
    const event = await events.append({
      type: "request_filed",
      actor,
      data: { request: entry, item, reason },
    });
    return { ok: true, request: entry, event };
  }

  async function editOffice(actor, args) {
    if (!isOfficeManager(actor)) throw new Error("edit_office is exclusive to the office manager");
    const current = await loadJson("office.json", null);
    if (!current) throw new Error("office.json missing");
    let next;
    let changes = [];
    if (isOfficeTweak(args)) {
      const applied = applyOfficeTweak(current, args);
      next = applied.office;
      changes = applied.changes;
    } else {
      const patch = args.office && typeof args.office === "object" ? args.office : args;
      const { move, whiteboard, deskItem, add, ...rest } = patch;
      next = {
        ...current,
        ...rest,
        desks: rest.desks || current.desks,
        rooms: rest.rooms || current.rooms,
        decor: rest.decor || current.decor,
        budget: { furniture: Number(current.budget?.furniture ?? 0), ...(rest.budget || {}) },
      };
      if (move || whiteboard || deskItem || add) {
        const applied = applyOfficeTweak(next, { move, whiteboard, deskItem, add });
        next = applied.office;
        changes = applied.changes;
      }
    }
    const error = validateOffice(next);
    if (error) {
      await rejectOffice(actor, error, args);
      throw new Error(error);
    }
    const added = Math.max(0, (next.decor?.length || 0) - (current.decor?.length || 0));
    const furniture = Number(next.budget?.furniture ?? 0);
    if (added > furniture) {
      const msg = `furniture budget ${furniture} cannot cover ${added} new props`;
      await rejectOffice(actor, msg, args);
      throw new Error(msg);
    }
    if (added > 0) next.budget.furniture = furniture - added;
    await saveJson("office.json", next);
    const primary = changes[0] || {};
    const event = await events.append({
      type: "office_edited",
      actor,
      data: {
        added,
        furniture: next.budget.furniture,
        kind: primary.kind,
        x: primary.x,
        y: primary.y,
        at: primary.at,
        action: primary.action,
        changes,
      },
    });
    return { ok: true, office: next, event, changes };
  }

  async function rejectOffice(actor, error, patch) {
    const { abs } = resolveWorkspacePath(workspaceRoot, "office-rejections.jsonl");
    await appendFile(abs, `${JSON.stringify({ ts: now(), actor, error, patch })}\n`).catch(() => {});
    await events.append({
      type: "office_edit_rejected",
      actor,
      data: { error },
    });
  }

  async function editSelf(actor, args) {
    const record = await loadEmployeeRecord(workspaceRoot, actor);
    if (!record) throw new Error(`no employee record for ${actor}`);
    const next = applyAestheticsPatch(record, args);
    const { abs } = resolveWorkspacePath(workspaceRoot, `employees/${actor}.json`);
    await writeFileAtomic(abs, `${JSON.stringify(next, null, 2)}\n`);
    const event = await events.append({
      type: "aesthetics_changed",
      actor,
      data: { aesthetics: next.aesthetics },
    });
    return { ok: true, aesthetics: next.aesthetics, event };
  }
}
