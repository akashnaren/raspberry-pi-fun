import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export async function createKillSwitch({ filePath }) {
  await mkdir(dirname(filePath), { recursive: true });
  const listeners = new Set();

  async function paused() {
    try {
      await readFile(filePath);
      return true;
    } catch (error) {
      if (error.code === "ENOENT") return false;
      throw error;
    }
  }

  async function setPaused(value, reason = "") {
    const was = await paused();
    if (value) {
      await writeFile(
        filePath,
        `${JSON.stringify({ paused: true, reason, ts: Date.now() }, null, 2)}\n`,
        "utf8",
      );
    } else {
      try {
        await unlink(filePath);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
    if (was !== value) {
      for (const listener of listeners) listener({ paused: value, reason });
    }
    return value;
  }

  return {
    paused,
    pause: (reason = "kill switch") => setPaused(true, reason),
    resume: () => setPaused(false, ""),
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
