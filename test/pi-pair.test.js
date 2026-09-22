import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { REPO } from "./helpers.js";

const execFileAsync = promisify(execFile);

test("pi-pair helpers and chat contract", async () => {
  const result = await execFileAsync(
    "python3",
    ["-m", "unittest", "discover", "-s", join(REPO, "pi-pair"), "-p", "test_*.py"],
    {
      cwd: REPO,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PI_PAIR_PEERS: "" },
    },
  );
  assert.match(result.stderr, /\bOK\b/);
});

test("fishbowl unit and MESH client stay on their current paths", async () => {
  const unit = await readFile(join(REPO, "deploy", "ai-studio.service"), "utf8");
  assert.match(unit, /WorkingDirectory=\/home\/pi\/raspberry-pi-fun/);
  assert.match(unit, /ExecStart=\/home\/pi\/raspberry-pi-fun\/deploy\/start\.sh/);
  assert.match(unit, /Environment=DRY_RUN=true/);
  const mesh = await readFile(join(REPO, "src", "mesh.js"), "utf8");
  assert.match(mesh, /\/v1\/chat\/completions/);
  assert.match(mesh, /X-Pi-Target/);
  assert.match(mesh, /X-Pi-Mesh/);
  const readme = await readFile(join(REPO, "pi-pair", "README.md"), "utf8");
  assert.match(readme, /python3 mini_chat\.py/);
  const ignore = await readFile(join(REPO, ".gitignore"), "utf8");
  assert.match(ignore, /__pycache__\//);
  assert.match(ignore, /\*\.gguf/);
  assert.match(ignore, /^pi-pair\/peers\.json$/m);
});
