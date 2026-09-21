import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createBudget } from "./budget.js";
import { createEventLog } from "./event-log.js";
import { createKillSwitch } from "./kill-switch.js";
import { fetchOpenRouterModels, resolveEmployeeModels } from "./models.js";
import { resolveStudioMode, runwayHours } from "./mode.js";
import { createDryRunDriver } from "./dry-run.js";
import { createLlm } from "./openrouter.js";
import { createOrchestrator } from "./orchestrator.js";
import { createStudioServer } from "./server.js";
import { createToolRunner } from "./tools.js";
import { createPromoter, syntaxCheckHtml } from "./validator.js";
import { createRelationships } from "./relationships.js";
import { mergeEmployees } from "./aesthetics.js";

export async function loadConfig(root, env = process.env) {
  const raw = JSON.parse(await readFile(join(root, "studio.config.json"), "utf8"));
  const minMs = Number(env.STUDIO_TICK_MIN_MS || raw.tick.minMs || raw.tick_ms || 90_000);
  const maxMs = Number(env.STUDIO_TICK_MAX_MS || raw.tick.maxMs || raw.tick_ms || 120_000);
  const midMs = Number(raw.tick?.midMs || raw.tick_ms || 105_000);
  const dailyCeilingUsd = Number(
    env.DAILY_CEILING_USD || raw.budget.dailyCeilingUsd || raw.budget.daily_ceiling_usd || raw.daily_ceiling_usd || 5,
  );
  const mode = resolveStudioMode(env);
  return {
    ...raw,
    tick: { minMs, maxMs, midMs },
    budget: { dailyCeilingUsd },
    openrouter: {
      base_url: raw.openrouter?.base_url || "https://openrouter.ai/api/v1",
    },
    host: env.HOST || "127.0.0.1",
    port: env.PORT === undefined || env.PORT === "" ? 8787 : Number(env.PORT),
    apiKey: env.OPENROUTER_API_KEY || "",
    referer: env.OPENROUTER_HTTP_REFERER || `http://127.0.0.1:${env.PORT || 8787}`,
    title: env.OPENROUTER_TITLE || "Meridian Desk",
    mode,
  };
}

export async function createStudio({
  root,
  env = process.env,
  fetchImpl = fetch,
  now = () => Date.now(),
  random = Math.random,
  validationTimeoutMs = 8000,
  listen = true,
} = {}) {
  const config = await loadConfig(root, env);
  const workspaceRoot = join(root, "workspace");
  const distRoot = join(root, "dist");
  const dataRoot = join(root, "data");
  const pendingValidations = new Map();

  const names = Object.fromEntries(config.employees.map((employee) => [employee.id, employee.name]));
  names.system = "Meridian Desk";
  const events = await createEventLog({ filePath: join(dataRoot, "events.jsonl"), now, names });
  const relationships = await createRelationships({
    filePath: join(workspaceRoot, "relationships.json"),
    now,
  });
  await relationships.decayIfNewDay();
  const budget = await createBudget({
    filePath: join(dataRoot, "spend.json"),
    dailyCeilingUsd: config.budget.dailyCeilingUsd,
    now,
  });
  const killSwitch = await createKillSwitch({ filePath: join(dataRoot, "PAUSED") });
  const promoter = createPromoter({ workspaceRoot, distRoot });

  let available = [];
  if (config.apiKey && config.mode.live) {
    try {
      available = await fetchOpenRouterModels(fetchImpl, config.openrouter.base_url);
    } catch {
      available = [];
    }
  }
  const roster = resolveEmployeeModels(config.employees, available);
  let employees = await mergeEmployees(workspaceRoot, roster);
  for (const employee of employees) {
    await events.append({
      type: "model_resolved",
      actor: employee.id,
      message: `${employee.name} thinks with ${employee.model} (${employee.modelFamily})`,
      data: { model: employee.model, family: employee.modelFamily, dryRun: !config.apiKey },
    });
  }

  const seeded = await promoter.seedDistIfMissing();
  if (seeded) {
    await events.append({
      type: "build_passed",
      actor: "system",
      message: "Meridian Office is on the right pane.",
      data: { path: "product/index.html", stage: "seed" },
    });
  }

  const live = {
    paused: await killSwitch.paused(),
    office: await readJson(join(workspaceRoot, "office.json")),
    backlog: (await readJson(join(workspaceRoot, "backlog.json"))) || { tasks: [] },
    relationships: relationships.snapshot(),
    acting: null,
  };

  async function refreshLive() {
    live.paused = await killSwitch.paused();
    live.office = await readJson(join(workspaceRoot, "office.json"));
    live.backlog = (await readJson(join(workspaceRoot, "backlog.json"))) || { tasks: [] };
    live.relationships = relationships.snapshot();
    employees = await mergeEmployees(workspaceRoot, roster);
  }

  events.subscribe((event) => {
    if (event.type === "turn_started") {
      live.acting = {
        id: event.actor,
        name: names[event.actor] || event.actor,
        at: event.data?.role === "qa" ? "the last green build" : "their desk",
        tool: null,
      };
    } else if (live.acting && event.actor === live.acting.id) {
      live.acting.tool = toolNameFor(event);
    }
    relationships.applyEvent(event).then(() => refreshLive()).catch(() => {});
    refreshLive().catch(() => {});
  });
  killSwitch.subscribe(() => {
    refreshLive().catch(() => {});
  });

  let server = null;

  async function requestBrowserValidation(id) {
    if (!server || server.wss.clients.size === 0) return { ok: true, skipped: true };
    return await new Promise((resolve) => {
      const timer = setTimeout(() => {
        pendingValidations.delete(id);
        resolve({ ok: false, error: "validation timeout" });
      }, validationTimeoutMs);
      pendingValidations.set(id, {
        resolve: (result) => {
          clearTimeout(timer);
          resolve(result);
        },
      });
      server.broadcast({
        type: "validate",
        id,
        url: `/candidate/index.html?v=${id}`,
      });
    });
  }

  const tools = createToolRunner({
    workspaceRoot,
    events,
    now,
    employees: roster,
    async onProductWrite({ actor, path }) {
      const html = await readFile(join(workspaceRoot, "product/index.html"), "utf8");
      const syntax = syntaxCheckHtml(html);
      if (!syntax.ok) {
        await events.append({
          type: "build_failed",
          actor,
          message: `build failed syntax check: ${syntax.error}`,
          data: { path, error: syntax.error, stage: "syntax" },
        });
        return { ok: false, error: syntax.error };
      }
      const id = `val-${now()}`;
      const browser = await requestBrowserValidation(id);
      if (!browser.ok) {
        await events.append({
          type: "build_failed",
          actor,
          message: `build failed iframe check: ${browser.error || "unknown"}`,
          data: { path, error: browser.error, stage: "iframe" },
        });
        return { ok: false, error: browser.error };
      }
      await promoter.promoteProduct();
      await events.append({
        type: "build_passed",
        actor,
        message: `green build promoted to dist/ (${path})`,
        data: { path, skippedBrowser: Boolean(browser.skipped) },
      });
      return { ok: true };
    },
  });

  const llm = createLlm({
    apiKey: config.apiKey,
    referer: config.referer,
    title: config.title,
    baseUrl: config.openrouter.base_url,
    fetchImpl,
    forceDryRun: config.mode.dryRun,
    dryRunDriver: createDryRunDriver({
      readProduct() {
        try {
          return readFileSync(join(workspaceRoot, "product/index.html"), "utf8");
        } catch {
          return "";
        }
      },
      readSheets() {
        try {
          return readFileSync(join(workspaceRoot, "product/sheets.html"), "utf8");
        } catch {
          return "";
        }
      },
      readInvoice() {
        try {
          return readFileSync(join(workspaceRoot, "product/invoice.html"), "utf8");
        } catch {
          return "";
        }
      },
    }),
  });

  const orchestrator = createOrchestrator({
    workspaceRoot,
    employees,
    lottery: config.lottery,
    tick: config.tick,
    events,
    tools,
    llm,
    budget,
    killSwitch,
    replay: config.mode.replay,
    localStubs: config.mode.localStubs,
    now,
    random,
  });

  function getSnapshot() {
    const budgetSnap = budget.snapshot();
    const sleeping = Boolean(budgetSnap.exhausted);
    const runway = runwayHours({
      spentUsd: budgetSnap.spentUsd,
      remainingUsd: budgetSnap.remainingUsd,
      now: now(),
    });
    return {
      studio: config.studio,
      dryRun: llm.dryRun || config.mode.dryRun,
      mode: config.mode.replay ? "replay" : config.mode.live ? "live" : "dry-run",
      replay: config.mode.replay,
      lite: config.mode.lite,
      paused: live.paused,
      sleeping,
      office: live.office,
      backlog: live.backlog,
      relationships: live.relationships,
      acting: live.acting,
      budget: budgetSnap,
      hud: {
        staff: employees.length,
        dayN: studioDayNumber(events, now()),
        currentTask: firstOpenTask(live.backlog),
        shipLine: "shipping when green",
        treasury: Number(live.office?.budget?.furniture ?? 0),
        burnUsd: budgetSnap.spentUsd,
        ceilingUsd: budgetSnap.ceilingUsd,
        remainingUsd: budgetSnap.remainingUsd,
        runwayHours: runway,
        sleeping: Boolean(budgetSnap.exhausted),
        models: employees.map((employee) => ({
          id: employee.id,
          name: employee.name,
          model: employee.model,
          family: employee.modelFamily,
        })),
      },
      employees,
      events: events.recent(40),
      tick: config.tick,
    };
  }

  server = createStudioServer({
    host: config.host,
    port: config.port,
    publicDir: join(root, "public"),
    workspaceRoot,
    distRoot,
    events,
    budget,
    killSwitch,
    pendingValidations,
    getSnapshot,
  });

  if (listen) {
    await server.listen();
  }

  await events.append({
    type: "world_started",
    actor: "system",
    message: config.mode.replay
      ? "Meridian Desk opened in replay. Zero tokens."
      : llm.dryRun
        ? "Meridian Desk opened in dry-run (DRY_RUN default or no key)"
        : "Meridian Desk went live via OpenRouter",
    data: {
      dryRun: llm.dryRun,
      replay: config.mode.replay,
      ceilingUsd: config.budget.dailyCeilingUsd,
      employees: employees.map((employee) => ({
        id: employee.id,
        model: employee.model,
        family: employee.modelFamily,
      })),
    },
  });

  return {
    config,
    employees,
    events,
    budget,
    killSwitch,
    tools,
    relationships,
    llm,
    orchestrator,
    server,
    promoter,
    snapshot: async () => {
      await refreshLive();
      return getSnapshot();
    },
    pendingValidations,
    async start() {
      orchestrator.start();
    },
    async stop() {
      orchestrator.stop();
      await server.close();
    },
  };
}

async function readJson(path) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    return null;
  }
}

function studioDayNumber(events, nowTs) {
  const started = events.all().find((event) => event.type === "world_started");
  const origin = started?.ts || nowTs;
  return 1 + Math.max(0, Math.floor((nowTs - origin) / 86_400_000));
}

function firstOpenTask(backlog) {
  const open = (backlog?.tasks || []).find((task) => task.status === "open");
  return open?.text || "shipping when green";
}

function toolNameFor(event) {
  const map = {
    file_written: "write_file",
    file_read: "read_file",
    say: "say",
    journal: "journal",
    task_added: "add_task",
    task_closed: "close_task",
    request_filed: "request",
    office_edited: "edit_office",
    aesthetics_changed: "edit_self_aesthetics",
  };
  return map[event.type] || event.data?.tool || event.type;
}
