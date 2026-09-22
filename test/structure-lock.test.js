import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { REPO } from "./helpers.js";

const PUBLIC_ENTRYPOINTS = [
  "office.js",
  "office-draw.js",
  "office-perf.js",
  "office-motion.js",
  "office-net.js",
  "office-seed.js",
  "index.html",
  "office.css",
];

function sha256(text) {
  return createHash("sha256").update(text).digest("hex");
}

test("sacred Fishbowl paths stay in place", async () => {
  const server = await readFile(join(REPO, "src/server.js"), "utf8");
  assert.match(server, /publicDir/);
  assert.match(server, /path: "\/ws"/);
  assert.match(server, /pathname === "\/api\/state"/);
  assert.match(server, /pathname\.startsWith\("\/dist\/"\)/);

  for (const name of PUBLIC_ENTRYPOINTS) {
    const text = await readFile(join(REPO, "public", name), "utf8");
    assert.ok(text.length > 20, name);
  }

  const unit = await readFile(join(REPO, "deploy/ai-studio.service"), "utf8");
  assert.match(unit, /WorkingDirectory=\/home\/pi\/raspberry-pi-fun/);
  assert.match(unit, /ExecStart=\/home\/pi\/raspberry-pi-fun\/deploy\/start\.sh/);
  assert.match(unit, /Environment=DRY_RUN=true/);

  const office = await readFile(join(REPO, "workspace/office.json"), "utf8");
  const parsed = JSON.parse(office);
  assert.ok(parsed && typeof parsed === "object");

  const mesh = await readFile(join(REPO, "src/mesh.js"), "utf8");
  assert.match(mesh, /env\.MESH_URL/);
  assert.match(mesh, /\/v1\/chat\/completions/);
  assert.match(mesh, /X-Pi-Target/);
  assert.match(mesh, /port === "18080"/);
});

test("pi-pair split keeps the pi4 entry and the canonical chat UI", async () => {
  const entry = await readFile(join(REPO, "pi-pair/mini_chat.py"), "utf8");
  assert.match(entry, /from pair\.server import main/);
  const config = await readFile(join(REPO, "pi-pair/pair/config.py"), "utf8");
  assert.match(config, /PI_PAIR_PORT", "18080"/);
  assert.match(config, /PI_PAIR_HOST", "0\.0\.0\.0"/);

  const html = await readFile(join(REPO, "pi-pair/static/index.html"), "utf8");
  const css = await readFile(join(REPO, "pi-pair/static/mesh.css"), "utf8");
  const js = await readFile(join(REPO, "pi-pair/static/mesh.js"), "utf8");
  assert.match(html, /\/static\/mesh\.css/);
  assert.match(html, /\/static\/mesh\.js/);
  assert.match(html, /__MODEL__/);
  assert.match(js, /window\.MESH_DEFAULT_MODEL/);
  assert.equal(sha256(css), "94a23bd4baf991f6871a3391702bade53d49b311cac1128c95f7a68dfda94d16");
  assert.equal(sha256(js), "7992943cc5711d8cca44f052c0bec365e5f40f3834a951fc855d9955d2b8c9cc");
  assert.equal(sha256(html), "ca4b2fb3b0fd3170f152cce736ec7b1c1e6a1db12da49cebe7fda0528d1b3199");
});
