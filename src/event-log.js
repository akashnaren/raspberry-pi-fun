import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const ROTATE_BYTES = 5 * 1024 * 1024;
const KEEP_ON_ROTATE = 2000;

export async function createEventLog({ filePath, now = () => Date.now() }) {
  await mkdir(dirname(filePath), { recursive: true });
  let events = await load(filePath);
  let seq = events.reduce((max, event) => Math.max(max, parseSeq(event.id)), 0);
  const listeners = new Set();

  async function persist(event) {
    await appendFile(filePath, `${JSON.stringify(event)}\n`, "utf8");
  }

  async function rotateIfNeeded() {
    try {
      const raw = await readFile(filePath, "utf8");
      if (Buffer.byteLength(raw, "utf8") < ROTATE_BYTES) return;
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
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}

async function load(filePath) {
  try {
    const raw = await readFile(filePath, "utf8");
    return raw
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line));
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

function parseSeq(id) {
  const match = /^e-(\d+)$/.exec(id ?? "");
  return match ? Number(match[1]) : 0;
}
