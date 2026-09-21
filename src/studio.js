import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createBudget } from "./budget.js";
import { createEventLog } from "./event-log.js";
import { createKillSwitch } from "./kill-switch.js";
import { fetchOpenRouterModels, resolveEmployeeModels } from "./models.js";
import { createLlm } from "./openrouter.js";
import { createOrchestrator } from "./orchestrator.js";
import { createStudioServer } from "./server.js";
import { createToolRunner } from "./tools.js";
import { createPromoter, syntaxCheckHtml } from "./validator.js";
import { createRelationships } from "./relationships.js";

export async function loadConfig(root, env = process.env) {
  const raw = JSON.parse(await readFile(join(root, "studio.config.json"), "utf8"));
  const minMs = Number(env.STUDIO_TICK_MIN_MS || raw.tick.minMs);
  const maxMs = Number(env.STUDIO_TICK_MAX_MS || raw.tick.maxMs);
  const dailyCeilingUsd = Number(env.DAILY_CEILING_USD || raw.budget.dailyCeilingUsd);
  return {
    ...raw,
    tick: { minMs, maxMs },
    budget: { dailyCeilingUsd },
    host: env.HOST || "127.0.0.1",
    port: env.PORT === undefined || env.PORT === "" ? 8787 : Number(env.PORT),
    apiKey: env.OPENROUTER_API_KEY || "",
    referer: env.OPENROUTER_HTTP_REFERER || `http://127.0.0.1:${env.PORT || 8787}`,
    title: env.OPENROUTER_TITLE || "Meridian Desk",
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
  if (config.apiKey) {
    try {
      available = await fetchOpenRouterModels(fetchImpl);
    } catch {
      available = [];
    }
  }
  const employees = resolveEmployeeModels(config.employees, available);
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
      message: "Timezone Buddy is on the right pane.",
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
  }

  events.subscribe((event) => {
    if (event.type === "turn_started") {
      live.acting = {
        id: event.actor,
        name: names[event.actor] || event.actor,
        at: event.data?.role === "qa" ? "the last green build" : "their desk",
      };
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
    fetchImpl,
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
    now,
    random,
  });

  function getSnapshot() {
    return {
      studio: config.studio,
      dryRun: llm.dryRun,
      paused: live.paused,
      office: live.office,
      backlog: live.backlog,
      relationships: live.relationships,
      acting: live.acting,
      budget: budget.snapshot(),
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
    message: llm.dryRun
      ? "Meridian Desk opened in dry-run (no OPENROUTER_API_KEY)"
      : "Meridian Desk went live via OpenRouter",
    data: {
      dryRun: llm.dryRun,
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
