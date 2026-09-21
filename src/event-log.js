import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { headlineFor } from "./headline.js";

const ROTATE_BYTES = 5 * 1024 * 1024;
const KEEP_ON_ROTATE = 2000;

export async function createEventLog({ filePath, now = () => Date.now(), names = {} }) {
  await mkdir(dirname(filePath), { recursive: true });
  const loaded = await load(filePath);
  let events = loaded.events;
  let seq = events.reduce((max, event) => Math.max(max, parseSeq(event.id)), 0);
  const listeners = new Set();

  async function persist(event) {
    await appendFile(filePath, `${JSON.stringify(event)}\n`, "utf8");
  }

  async function rotateIfNeeded() {
    try {
      const raw = await readFile(filePath);
      if (raw.length < ROTATE_BYTES) return;
      const kept = events.slice(-KEEP_ON_ROTATE);
      const tmp = `${filePath}.tmp`;
      await writeFile(tmp, kept.map((event) => JSON.stringify(event)).join("\n") + "\n", "utf8");
      await rename(tmp, filePath);
      events = kept;
    } catch {
      // Rotation is best-effort; the live log stays the truth.
    }
  }

  return {
    async append(partial) {
      seq += 1;
      const event = {
        id: `e-${String(seq).padStart(6, "0")}`,
        ts: now(),
        type: partial.type,
        actor: partial.actor ?? "system",
        message: partial.message ?? "",
        data: partial.data ?? {},
      };
      event.headline = headlineFor(event, names);
      if (!partial.message) event.message = event.headline;
      events.push(event);
      await persist(event);
      await rotateIfNeeded();
      for (const listener of listeners) listener(event);
      return event;
    },
    recent(n = 10) {
      return events.slice(-n);
    },
    all() {
      return events.slice();
    },
    quarantined() {
      return loaded.quarantined.slice();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

/**
 * Split a JSONL buffer into good events and bad lines.
 * Power cuts on a Pi often leave a trailing null-padded or half-written line.
 * Those must never take down ai-studio.service.
 */
export function parseJsonl(raw) {
  const text = (Buffer.isBuffer(raw) ? raw.toString("utf8") : String(raw ?? "")).replace(/^\uFEFF/, "");
  const events = [];
  const quarantined = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    if (line.includes("\u0000")) {
      quarantined.push({ reason: "null-byte", line: line.replace(/\u0000/g, "") });
      continue;
    }
    if (line.length > 1_000_000) {
      quarantined.push({ reason: "too-long", line: line.slice(0, 240) });
      continue;
    }
    try {
      const parsed = JSON.parse(line);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        quarantined.push({ reason: "not-object", line });
        continue;
      }
      events.push(parsed);
    } catch {
      quarantined.push({ reason: "json", line });
    }
  }
  return { events, quarantined };
}

async function load(filePath) {
  try {
    const raw = await readFile(filePath);
    const parsed = parseJsonl(raw);
    if (parsed.quarantined.length) {
      const dump = parsed.quarantined
        .map((item) => JSON.stringify({ reason: item.reason, line: item.line, ts: Date.now() }))
        .join("\n");
      await appendFile(`${filePath}.corrupt`, `${dump}\n`, "utf8");
      try {
        const kept = parsed.events.map((event) => JSON.stringify(event)).join("\n");
        const tmp = `${filePath}.clean`;
        await writeFile(tmp, kept ? `${kept}\n` : "", "utf8");
        await rename(tmp, filePath);
      } catch {
        // Skip-bad-line is enough. Rewrite is best-effort.
      }
    }
    return parsed;
  } catch (error) {
    if (error.code === "ENOENT") return { events: [], quarantined: [] };
    throw error;
  }
}

function parseSeq(id) {
  const match = /^e-(\d+)$/.exec(id ?? "");
  return match ? Number(match[1]) : 0;
}
