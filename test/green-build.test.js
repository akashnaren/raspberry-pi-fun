import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createStudio } from "../src/studio.js";
import { tempStudioRoot, testEnv } from "./helpers.js";

test("green-build promotes only a passing product and leaves dist on failure", async () => {
  const root = await tempStudioRoot();
  const studio = await createStudio({
    root,
    env: testEnv(),
    listen: false,
    now: () => 1_700_000_000_000,
  });

  const before = await readFile(join(root, "dist/index.html"), "utf8");
  const beforeSheets = await readFile(join(root, "dist/sheets.html"), "utf8");
  const beforeSlides = await readFile(join(root, "dist/slides.html"), "utf8");
  assert.match(before, /Meridian Office/);
  assert.match(beforeSheets, /id="paste-from"/);
  assert.match(beforeSlides, /id="slide-title"/);

  const broken = await studio.tools.execute("nova", "write_file", {
    path: "product/index.html",
    contents: "<div>broken",
  });
  assert.equal(broken.build, "failed");
  const working = await readFile(join(root, "workspace/product/index.html"), "utf8");
  assert.match(working, /broken/);
  const afterFail = await readFile(join(root, "dist/index.html"), "utf8");
  assert.equal(afterFail, before);
  assert.equal(studio.events.all().some((event) => event.type === "build_failed"), true);

  const good = `<!doctype html><html><head><title>Meridian Office</title></head><body><h1>Meridian Office v2</h1></body></html>`;
  const passed = await studio.tools.execute("nova", "write_file", {
    path: "product/index.html",
    contents: good,
  });
  assert.equal(passed.ok, true);
  const afterPass = await readFile(join(root, "dist/index.html"), "utf8");
  assert.match(afterPass, /Meridian Office v2/);
  assert.equal(studio.events.all().some((event) => event.type === "build_passed"), true);

  await studio.stop();
});

test("dry-run tick works without an API key", async () => {
  const root = await tempStudioRoot();
  const studio = await createStudio({
    root,
    env: testEnv(),
    listen: false,
    random: () => 0,
  });
  assert.equal(studio.llm.dryRun, true);
  assert.equal(studio.config.studio.name, "Meridian Desk");
  assert.equal(studio.config.studio.product, "Meridian Office");
  const families = new Set(studio.employees.map((employee) => employee.modelFamily));
  assert.equal(families.size, 4);
  assert.equal(studio.employees.some((person) => person.id === "jules" && person.role === "office_manager"), true);
  assert.equal(studio.employees.find((person) => person.id === "nova").model, "qwen/qwen3-coder-next");
  assert.equal(studio.employees.find((person) => person.id === "jules").color, "#6366F1");
  const snap = await studio.snapshot();
  assert.equal(snap.hud.dayN, 1);
  assert.match(snap.hud.currentTask, /Docs first/);
  assert.equal(snap.hud.shipLine, "shipping when green");
  assert.equal(studio.config.tick.midMs, 105000);
  assert.equal(studio.config.budget.dailyCeilingUsd, 5);
  assert.equal(studio.config.openrouter.base_url, "https://openrouter.ai/api/v1");
  const novaChip = snap.hud.models.find((person) => person.id === "nova");
  assert.equal(novaChip.model, "qwen/qwen3-coder-next");
  assert.equal(snap.hud.models.some((person) => person.id === "river"), false);
  assert.equal(snap.employees.find((person) => person.id === "mira").model, "z-ai/glm-5.3-flash");
  assert.equal(snap.employees.find((person) => person.id === "kessler").model, "nousresearch/hermes-3-llama-3.1-70b");
  const novaKessler = studio.relationships
    .opinionsFor("nova")
    .find((item) => item.other === "kessler");
  assert.equal(novaKessler.score, -1);

  const result = await studio.orchestrator.tickOnce();
  assert.ok(result.employee);
  assert.equal(result.result.dryRun, true);
  const types = new Set(studio.events.all().map((event) => event.type));
  assert.equal(types.has("turn_started"), true);
  assert.equal(types.has("turn_finished"), true);
  await studio.stop();
});
