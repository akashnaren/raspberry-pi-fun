import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { opinionsFor, emptyMatrix } from "../src/relationships.js";
import { REPO } from "./helpers.js";

test("cast lock: Nova, Kessler, Mira, Jules — product is Meridian Office", async () => {
  const config = JSON.parse(await readFile(join(REPO, "studio.config.json"), "utf8"));
  assert.equal(config.studio.name, "Meridian Desk");
  assert.equal(config.studio.product, "Meridian Office");
  const byId = Object.fromEntries(config.employees.map((person) => [person.id, person]));
  assert.equal(byId.nova.name, "Nova Chen");
  assert.equal(byId.nova.role, "programmer");
  assert.equal(byId.nova.accent || byId.nova.color, "#F97316");
  assert.equal(byId.nova.modelTier, "frontier");
  assert.match(byId.nova.priorities, /tonight|Working beats pretty/i);
  assert.equal(byId.kessler.name, "Kessler Holt");
  assert.equal(byId.kessler.role, "qa");
  assert.equal(byId.kessler.accent || byId.kessler.color, "#14B8A6");
  assert.equal(byId.kessler.modelTier, "mid");
  assert.match(byId.kessler.priorities, /loose greens/i);
  assert.equal(byId.mira.name, "Mira Sol");
  assert.equal(byId.mira.role, "producer");
  assert.equal(byId.mira.accent || byId.mira.color, "#F59E0B");
  assert.equal(byId.mira.modelTier, "cheap-capable");
  assert.match(byId.mira.priorities, /scope creep/i);
  assert.equal(byId.jules.name, "Jules Park");
  assert.equal(byId.jules.role, "office_manager");

  assert.equal(byId.river, undefined);
  assert.equal(config.employees.some((person) => person.id === "river"), false);
  assert.equal(byId.nova.model, "qwen/qwen3-coder-next");
  assert.equal(byId.mira.model, "z-ai/glm-5.3-flash");
  assert.equal(byId.kessler.model, "nousresearch/hermes-3-llama-3.1-70b");
  assert.equal(byId.nova.max_tokens ?? byId.nova.maxTokens, 1500);
  assert.equal(byId.mira.max_tokens ?? byId.mira.maxTokens, 500);
  assert.equal(byId.kessler.max_tokens ?? byId.kessler.maxTokens, 400);
  assert.equal(byId.nova.lottery_weight ?? config.lottery.nova, 0.5);
  assert.equal(byId.mira.lottery_weight ?? config.lottery.mira, 0.25);
  assert.equal(byId.kessler.lottery_weight ?? config.lottery.kessler, 0.25);
  assert.equal(config.lottery.nova, 0.5);
  assert.equal(config.lottery.mira, 0.25);
  assert.equal(config.lottery.kessler, 0.25);
  assert.equal(config.tick.midMs || config.tick_ms, 105000);
  assert.equal(config.tick.minMs, 90000);
  assert.equal(config.tick.maxMs, 120000);
  assert.equal(config.daily_ceiling_usd ?? config.budget.daily_ceiling_usd ?? config.budget.dailyCeilingUsd, 5);
  assert.equal(config.openrouter.base_url, "https://openrouter.ai/api/v1");
  assert.ok(byId.nova.preferredModels.includes("qwen/qwen3-coder-next"));
  assert.ok(byId.mira.preferredModels.includes("z-ai/glm-5.3-flash"));
  assert.ok(byId.kessler.preferredModels.includes("nousresearch/hermes-3-llama-3.1-70b"));
  const stage1Families = new Set(["qwen", "z-ai", "nousresearch"]);
  assert.equal(stage1Families.has(byId.nova.modelFamily) && byId.nova.modelFamily === "qwen", true);
  assert.equal(byId.mira.modelFamily, "z-ai");
  assert.equal(byId.kessler.modelFamily, "nousresearch");
  assert.equal(new Set([byId.nova.modelFamily, byId.mira.modelFamily, byId.kessler.modelFamily]).size, 3);

  const hud = await readFile(join(REPO, "public/index.html"), "utf8");
  assert.match(hud, /id="event-line"/);
  assert.match(hud, /id="acting"/);
  assert.doesNotMatch(hud, /ticker-track|ticker-wrap|on-air|ON AIR|model-chips|Meridian Desk/);
  const officeJs = await readFile(join(REPO, "public/office.js"), "utf8");
  assert.match(officeJs, /employee\.model \|\| employee\.modelFamily/);
  assert.match(officeJs, /destinationFor/);
  assert.doesNotMatch(officeJs, /camera\.tx|tickerEl|onAir|paintModelChips|paintTicker/);
  const css = await readFile(join(REPO, "public/office.css"), "utf8");
  assert.doesNotMatch(css, /@keyframes crawl|ON AIR|\.on-air|\.ticker-track/);

  const office = JSON.parse(await readFile(join(REPO, "workspace/office.json"), "utf8"));
  for (const id of ["nova", "kessler", "mira", "jules"]) {
    const desk = office.desks.find((item) => item.owner === id);
    assert.ok(desk, `${id} desk`);
    assert.ok(desk.items.includes("monitor"));
    assert.ok(desk.items.includes("coffee_mug"));
    assert.ok(desk.items.includes("plant"));
  }
  assert.ok(office.decor.some((item) => item.kind === "whiteboard" && /Meridian Office/.test(item.text)));
  assert.ok(office.rooms.some((room) => /break/i.test(room.name)));
  assert.ok(office.decor.some((item) => item.kind === "coffee"));

  const product = await readFile(join(REPO, "workspace/product/index.html"), "utf8");
  assert.match(product, /Meridian Office/);
  assert.match(product, /contenteditable/);
  assert.doesNotMatch(product, /sign up|create an account|checkout|stripe|chat box|live chat/i);

  const matrix = emptyMatrix();
  assert.equal(opinionsFor(matrix, "nova").find((item) => item.other === "kessler").score, -1);
  assert.equal(opinionsFor(matrix, "mira").find((item) => item.other === "nova").score, 1);
  assert.equal(opinionsFor(matrix, "mira").find((item) => item.other === "kessler").score, 0);
});
