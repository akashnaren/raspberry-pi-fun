import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const srcDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../src");

function filesIn(dir) {
  const found = [];
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) found.push(...filesIn(full));
    else if (name.endsWith(".ts")) found.push(full);
  }
  return found;
}

// main.ts is last so a slice from a moved function to a boot call still runs forward.
export function webSource() {
  const files = filesIn(srcDir).sort((a, b) => a.localeCompare(b));
  const rest = [];
  const main = [];
  for (const file of files) (path.basename(file) === "main.ts" ? main : rest).push(file);
  return [...rest, ...main].map((file) => fs.readFileSync(file, "utf8")).join("\n");
}
