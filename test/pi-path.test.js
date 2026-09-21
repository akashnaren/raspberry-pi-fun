import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { REPO } from "./helpers.js";

const execFileAsync = promisify(execFile);
const START = join(REPO, "deploy", "start.sh");

async function fakeNode(dir) {
  const bin = join(dir, "bin");
  await mkdir(bin, { recursive: true });
  const node = join(bin, "node");
  await writeFile(node, `#!/bin/bash\nprintf '%s\\n' "$0"\n`);
  await chmod(node, 0o755);
  return node;
}

test("start.sh prefers $HOME/.local/node-v20.20.2 then ~/.local/node", async () => {
  const home = await mkdtemp(join(tmpdir(), "fishbowl-node-"));
  const versioned = await fakeNode(join(home, ".local", "node-v20.20.2"));
  const linkedHome = join(home, ".local", "node");
  await fakeNode(linkedHome);

  const { stdout } = await execFileAsync("bash", [START], {
    env: { HOME: home, PATH: "/usr/bin:/bin", FISHBOWL_RESOLVE_ONLY: "1" },
  });
  assert.equal(stdout.trim(), versioned);

  const homeLinkOnly = await mkdtemp(join(tmpdir(), "fishbowl-node-"));
  const linked = await fakeNode(join(homeLinkOnly, ".local", "node"));
  const again = await execFileAsync("bash", [START], {
    env: { HOME: homeLinkOnly, PATH: "/usr/bin:/bin", FISHBOWL_RESOLVE_ONLY: "1" },
  });
  assert.equal(again.stdout.trim(), linked);
});

test("ai-studio unit uses user-local Node and optional EnvironmentFile", async () => {
  const unit = await readFile(join(REPO, "deploy", "ai-studio.service"), "utf8");
  assert.match(unit, /node-v20\.20\.2/);
  assert.match(unit, /\.local\/node/);
  assert.match(unit, /Environment=DRY_RUN=true/);
  assert.match(unit, /EnvironmentFile=-\/home\/pi\/\.secrets\/fishbowl\/openrouter\.env/);
  assert.match(unit, /EnvironmentFile=-\/home\/pi\/raspberry-pi-fun\/\.env/);
  assert.match(unit, /deploy\/start\.sh/);
  assert.doesNotMatch(unit, /\/usr\/local/);
  assert.doesNotMatch(unit, /sudo/);
});

test("locked office manager is Jules Park with indigo accent", async () => {
  const config = JSON.parse(await readFile(join(REPO, "studio.config.json"), "utf8"));
  const manager = config.employees.find((person) => person.role === "office_manager");
  assert.equal(manager.id, "jules");
  assert.equal(manager.name, "Jules Park");
  assert.equal(manager.color, "#6366F1");
  assert.equal(config.employees.length, 4);
});
