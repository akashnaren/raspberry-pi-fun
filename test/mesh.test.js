import { test } from "node:test";
import assert from "node:assert/strict";
import { request } from "node:http";
import { access, readFile } from "node:fs/promises";
import { join } from "node:path";
import { WebSocket } from "ws";
import { createLlm } from "../src/openrouter.js";
import { createStudio, loadConfig } from "../src/studio.js";
import { viewerOrigin } from "../src/server.js";
import {
  MeshError,
  buildMeshChatRequest,
  meshChat,
  meshNetworkAllowed,
  meshSettingsFromEnv,
  readMeshText,
} from "../src/mesh.js";
import {
  DOCS_ASSIST_STUB,
  IDLE_FLAVOR_STUB,
  clipMeshLine,
  docsExcerpt,
  executeMeshJobs,
  runMeshJob,
} from "../src/mesh-jobs.js";
import { createEventLog } from "../src/event-log.js";
import { tempStudioRoot, testEnv, REPO } from "./helpers.js";

const PAIR = "http://10.0.0.180:18080";
const OLLAMA = "http://10.0.0.180:11434";

function jsonResponse(body, { status = 200, headers = {} } = {}) {
  const lower = Object.fromEntries(
    Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]),
  );
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => lower[String(name).toLowerCase()] ?? null },
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  };
}

test("pair URL and target header default to auto across the fleet", () => {
  const settings = meshSettingsFromEnv({ MESH_URL: `${PAIR}/` });
  assert.equal(settings.enabled, true);
  assert.equal(settings.url, PAIR);
  assert.equal(settings.target, "auto");
  assert.equal(settings.kind, "pair");
  const req = buildMeshChatRequest(settings, {
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal(req.url, `${PAIR}/v1/chat/completions`);
  assert.equal(req.headers["X-Pi-Target"], "auto");
  assert.equal(req.headers["X-Pi-Mesh"], "on");
  assert.equal(req.body.pi_target, "auto");
  assert.equal(req.body.pi_mesh, "on");
  assert.equal(req.body.stream, false);
  assert.equal(req.body.model, "qwen2.5:0.5b");

  for (const target of ["pi2", "pi3", "pi4"]) {
    const pinned = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_TARGET: target });
    const built = buildMeshChatRequest(pinned, { messages: [] });
    assert.equal(built.headers["X-Pi-Target"], target);
    assert.equal(built.body.pi_target, target);
  }
});

test("port 11434 selects Ollama /api/chat; MESH_KIND can override", () => {
  const ollama = meshSettingsFromEnv({ MESH_URL: OLLAMA });
  assert.equal(ollama.kind, "ollama");
  const direct = buildMeshChatRequest(ollama, {
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal(direct.url, `${OLLAMA}/api/chat`);
  assert.equal(direct.headers["X-Pi-Target"], undefined);
  assert.equal(direct.body.stream, false);
  assert.equal(direct.body.pi_target, undefined);
  assert.equal(readMeshText("ollama", { message: { content: "hi from pi2" } }), "hi from pi2");

  const forcedPair = meshSettingsFromEnv({ MESH_URL: OLLAMA, MESH_KIND: "pair", MESH_TARGET: "pi3" });
  const pairReq = buildMeshChatRequest(forcedPair, { messages: [] });
  assert.equal(pairReq.url, `${OLLAMA}/v1/chat/completions`);
  assert.equal(pairReq.headers["X-Pi-Target"], "pi3");

  const forcedOllama = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_KIND: "ollama" });
  assert.equal(buildMeshChatRequest(forcedOllama, { messages: [] }).url, `${PAIR}/api/chat`);
});

test("bad target, kind, and URL fail clearly", () => {
  assert.throws(() => meshSettingsFromEnv({ MESH_TARGET: "pi5" }), MeshError);
  assert.throws(() => meshSettingsFromEnv({ MESH_URL: PAIR, MESH_KIND: "dgx" }), /pair or ollama/);
  assert.throws(() => meshSettingsFromEnv({ MESH_URL: "10.0.0.180:18080" }), /http/);
  const off = meshSettingsFromEnv({});
  assert.equal(off.enabled, false);
  assert.equal(off.target, "auto");
});

test("mesh chat returns text and names a pinned peer that is offline", async () => {
  const settings = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_TARGET: "auto" });
  const ok = await meshChat({
    settings,
    messages: [{ role: "user", content: "hi" }],
    fetchImpl: async (url, init) => {
      assert.equal(url, `${PAIR}/v1/chat/completions`);
      assert.equal(init.headers["X-Pi-Target"], "auto");
      return jsonResponse(
        { choices: [{ message: { content: "The lamp stays on." } }] },
        { headers: { "X-Pi-Peer": "pi3" } },
      );
    },
  });
  assert.equal(ok.text, "The lamp stays on.");
  assert.equal(ok.peer, "pi3");

  const calls = [];
  const pinned = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_TARGET: "pi3" });
  await assert.rejects(
    () =>
      meshChat({
        settings: pinned,
        messages: [{ role: "user", content: "hi" }],
        fetchImpl: async (url) => {
          calls.push(String(url));
          return jsonResponse({ error: "pi3 offline" }, { status: 503 });
        },
      }),
    (error) => error instanceof MeshError && error.code === "peer_offline" && error.message === "pi3 offline",
  );
  assert.deepEqual(calls, [`${PAIR}/v1/chat/completions`]);
});

test("a pinned peer timeout is a hard error and auto does not invent a peer name", async () => {
  const pinned = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_TARGET: "pi4" });
  await assert.rejects(
    () =>
      meshChat({
        settings: pinned,
        messages: [],
        timeoutMs: 20,
        fetchImpl: (_url, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener("abort", () => {
              const error = new Error("aborted");
              error.name = "AbortError";
              reject(error);
            });
          }),
      }),
    (error) => error.code === "peer_offline" && error.message === "pi4 offline",
  );

  const spread = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_TARGET: "auto" });
  await assert.rejects(
    () =>
      meshChat({
        settings: spread,
        messages: [],
        timeoutMs: 20,
        fetchImpl: (_url, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener("abort", () => {
              const error = new Error("aborted");
              error.name = "AbortError";
              reject(error);
            });
          }),
      }),
    (error) => error.code === "timeout" && error.message === "mesh timeout",
  );
});

test("unset MESH_URL leaves the dry-run tick and event log alone", async () => {
  const root = await tempStudioRoot();
  let fetches = 0;
  const studio = await createStudio({
    root,
    env: testEnv(),
    listen: false,
    random: () => 0,
    fetchImpl: async () => {
      fetches += 1;
      throw new Error("no network when MESH_URL is unset");
    },
  });
  const before = studio.events.all().map((event) => event.type);
  const jobs = await studio.runMeshJobs();
  assert.equal(jobs.skipped, "mesh-unset");
  assert.deepEqual(
    studio.events.all().map((event) => event.type),
    before,
  );
  const tick = await studio.orchestrator.tickOnce();
  assert.equal(tick.employee.id, "nova");
  assert.equal(tick.result.dryRun, true);
  assert.equal(tick.result.costUsd, 0);
  assert.equal(fetches, 0);
  assert.equal(studio.budget.snapshot().spentUsd, 0);
  assert.equal(studio.config.host, "127.0.0.1");
  await studio.stop();
});

test("DRY_RUN with MESH_URL set skips the network and does not append", async () => {
  const root = await tempStudioRoot();
  let fetches = 0;
  const studio = await createStudio({
    root,
    env: testEnv({ MESH_URL: PAIR, MESH_TARGET: "pi2" }),
    listen: false,
    fetchImpl: async () => {
      fetches += 1;
      throw new Error("dry-run must not call the mesh");
    },
  });
  const before = studio.events.all().length;
  const jobs = await studio.runMeshJobs();
  assert.equal(jobs.idle.skipped, "dry-run");
  assert.equal(jobs.idle.text, IDLE_FLAVOR_STUB);
  assert.equal(jobs.idle.committed, false);
  assert.equal(jobs.docs.text, DOCS_ASSIST_STUB);
  assert.equal(jobs.docs.network, false);
  assert.equal(fetches, 0);
  assert.equal(studio.events.all().length, before);
  assert.equal(meshNetworkAllowed(testEnv({ MESH_URL: PAIR })), false);
  await studio.stop();
});

test("live mesh writes idle flavor and docs assist into the stage log only", async () => {
  const root = await tempStudioRoot();
  const docsPath = join(root, "workspace/product/index.html");
  const beforeDocs = await readFile(docsPath, "utf8");
  const calls = [];
  const studio = await createStudio({
    root,
    env: testEnv({
      MESH_URL: PAIR,
      MESH_TARGET: "auto",
      DRY_RUN: "false",
    }),
    listen: false,
    fetchImpl: async (url, init) => {
      calls.push({ url: String(url), init });
      assert.doesNotMatch(String(url), /openrouter\.ai/);
      const body = JSON.parse(init.body);
      const prompt = body.messages.at(-1).content;
      const text = /Docs assist/.test(prompt) ? "Keep the next heading short." : "The lamp stays on.";
      return jsonResponse(
        { choices: [{ message: { content: text } }], model: "qwen2.5:0.5b" },
        { headers: { "X-Pi-Peer": "pi4" } },
      );
    },
  });
  assert.equal(studio.llm.dryRun, true);
  const spent = studio.budget.snapshot().spentUsd;
  const jobs = await studio.runMeshJobs();
  assert.equal(calls.length, 2);
  assert.equal(jobs.idle.via, "mesh");
  assert.equal(jobs.idle.text, "The lamp stays on.");
  assert.equal(jobs.idle.costUsd, 0);
  assert.equal(jobs.docs.text, "Keep the next heading short.");
  for (const call of calls) {
    assert.equal(call.url, `${PAIR}/v1/chat/completions`);
    assert.equal(call.init.headers["X-Pi-Target"], "auto");
    assert.equal(call.init.headers["X-Pi-Mesh"], "on");
    assert.equal(JSON.parse(call.init.body).pi_target, "auto");
  }
  const flavor = studio.events.all().filter((event) => event.type === "idle_flavor");
  const assist = studio.events.all().filter((event) => event.type === "docs_assist");
  assert.equal(flavor.length, 1);
  assert.equal(assist.length, 1);
  assert.equal(flavor[0].actor, "system");
  assert.equal(flavor[0].data.authority, "stage");
  assert.equal(flavor[0].data.peer, "pi4");
  assert.equal(flavor[0].headline, "The lamp stays on.");
  assert.equal(assist[0].data.authority, "stage");
  assert.equal(studio.budget.snapshot().spentUsd, spent);
  assert.equal(await readFile(docsPath, "utf8"), beforeDocs);
  await studio.stop();
});

test("pinned offline is recorded and OpenRouter is not called", async () => {
  const root = await tempStudioRoot();
  const calls = [];
  const studio = await createStudio({
    root,
    env: testEnv({
      MESH_URL: PAIR,
      MESH_TARGET: "pi3",
      DRY_RUN: "false",
    }),
    listen: false,
    fetchImpl: async (url) => {
      calls.push(String(url));
      return jsonResponse({ error: "pi3 offline" }, { status: 503 });
    },
  });
  const jobs = await studio.runMeshJobs();
  assert.equal(jobs.idle.error.code, "peer_offline");
  assert.equal(jobs.idle.error.message, "pi3 offline");
  assert.equal(jobs.docs.error.message, "pi3 offline");
  assert.deepEqual(calls, [`${PAIR}/v1/chat/completions`, `${PAIR}/v1/chat/completions`]);
  const errors = studio.events.all().filter((event) => event.type === "mesh_error");
  assert.equal(errors.length, 2);
  assert.equal(errors[0].headline, "pi3 offline");
  assert.equal(errors[0].data.authority, "stage");
  assert.equal(studio.events.all().some((event) => event.type === "idle_flavor"), false);
  await studio.stop();
});

test("the cast lottery still uses OpenRouter when a key is set", async () => {
  const root = await tempStudioRoot();
  const urls = [];
  const studio = await createStudio({
    root,
    env: testEnv({
      MESH_URL: PAIR,
      MESH_TARGET: "auto",
      DRY_RUN: "false",
      OPENROUTER_API_KEY: "sk-test",
    }),
    listen: false,
    random: () => 0,
    fetchImpl: async (url) => {
      urls.push(String(url));
      if (String(url).includes("10.0.0.180")) {
        return jsonResponse({ choices: [{ message: { content: "The lamp stays on." } }] });
      }
      if (String(url).includes("/models")) {
        return { ok: true, json: async () => ({ data: [] }) };
      }
      return {
        ok: true,
        json: async () => ({
          choices: [{ message: { content: "Noted." } }],
          usage: { prompt_tokens: 10, completion_tokens: 4 },
        }),
      };
    },
  });
  const tick = await studio.orchestrator.tickOnce();
  assert.equal(tick.result.dryRun, false);
  assert.ok(urls.some((url) => url.startsWith("https://openrouter.ai/")));
  assert.equal(urls.some((url) => url.includes("10.0.0.180")), false);
  const before = urls.length;
  await studio.runMeshJobs();
  const meshUrls = urls.slice(before);
  assert.ok(meshUrls.length >= 1);
  assert.ok(meshUrls.every((url) => url.startsWith(PAIR)));
  await studio.stop();
});

test("OpenRouter complete stays on its own host", async () => {
  const urls = [];
  const llm = createLlm({
    apiKey: "sk-test",
    forceDryRun: false,
    fetchImpl: async (url) => {
      urls.push(String(url));
      return {
        ok: true,
        json: async () => ({
          choices: [{ message: { content: "ok" } }],
          usage: {},
        }),
      };
    },
  });
  const result = await llm.complete({
    employee: { id: "mira", name: "Mira Sol", role: "producer", model: "z-ai/glm-5.3-flash" },
    messages: [{ role: "user", content: "hi" }],
  });
  assert.equal(result.dryRun, false);
  assert.match(urls[0], /^https:\/\/openrouter\.ai\/api\/v1\/chat\/completions$/);
});

test("default bind is all interfaces and a tunnel host still serves the socket", async () => {
  assert.equal(viewerOrigin({ headers: { host: "127.0.0.1:8787" } }), "http://127.0.0.1:8787");
  assert.equal(
    viewerOrigin({
      headers: {
        host: "127.0.0.1:8787",
        "x-forwarded-proto": "https",
        "x-forwarded-host": "meridian.example",
      },
    }),
    "https://meridian.example",
  );

  const root = await tempStudioRoot();
  const config = await loadConfig(root, testEnv({ HOST: "" }));
  assert.equal(config.host, "0.0.0.0");
  assert.equal(config.port, 0);

  const studio = await createStudio({
    root,
    env: testEnv({ HOST: "0.0.0.0", PORT: "0" }),
    listen: true,
  });
  const address = studio.server.server.address();
  assert.equal(address.address, "0.0.0.0");
  const port = address.port;

  const health = await httpCall(port, "/health", {
    host: "meridian.example",
    "x-forwarded-proto": "https",
    "x-forwarded-host": "meridian.example",
  });
  assert.equal(health.status, 200);
  const healthBody = JSON.parse(health.body);
  assert.equal(healthBody.ok, true);
  assert.equal(healthBody.origin, "https://meridian.example");

  const state = await httpCall(port, "/api/state", { host: "bad host" });
  assert.equal(state.status, 200);

  const dist = await httpCall(port, "/dist", { host: "meridian.example" });
  assert.equal(dist.status, 302);
  assert.equal(dist.headers.location, "/dist/");

  const socket = new WebSocket(`ws://127.0.0.1:${port}/ws`, {
    headers: { Host: "meridian.example" },
  });
  const hello = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("websocket timeout")), 3000);
    socket.on("message", (data) => {
      clearTimeout(timer);
      resolve(JSON.parse(String(data)));
    });
    socket.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
  });
  assert.equal(hello.type, "hello");
  assert.ok(hello.state);
  await new Promise((resolve) => {
    socket.once("close", resolve);
    socket.close();
  });
  await studio.stop();

  const office = await readFile(join(REPO, "public/office.js"), "utf8");
  assert.match(office, /location\.host/);
  assert.doesNotMatch(office, /new WebSocket\(`ws:\/\/127/);
});

test("docs excerpt stays short and mesh lines drop markup", () => {
  const excerpt = docsExcerpt("<title>Meridian Office — Docs</title><script>secret()</script><p>Headings then a list.</p>");
  assert.match(excerpt, /Meridian Office/);
  assert.match(excerpt, /Headings then a list/);
  assert.doesNotMatch(excerpt, /secret/);
  assert.equal(clipMeshLine("<b>Keep it short.</b> Then ignore this second sentence."), "Keep it short.");
});

test("M5 paused mesh skips idle flavor and docs assist without a fetch", async () => {
  const root = await tempStudioRoot();
  const events = await createEventLog({ filePath: join(root, "data", "events.jsonl") });
  const settings = meshSettingsFromEnv({ MESH_URL: PAIR, MESH_TARGET: "auto" });
  let fetches = 0;
  let paused = true;
  const fetchImpl = async () => {
    fetches += 1;
    throw new Error("paused mesh must not fetch");
  };
  const gate = async () => paused;

  const jobs = await executeMeshJobs({
    settings,
    allowNetwork: true,
    events,
    fetchImpl,
    meshPaused: gate,
    docsHtml: "<title>Meridian Office — Docs</title><p>Keep the list.</p>",
  });
  assert.equal(jobs.skipped, "mesh-paused");
  assert.equal(jobs.idle.skipped, "mesh-paused");
  assert.equal(jobs.docs.skipped, "mesh-paused");
  assert.equal(jobs.idle.network, false);
  assert.equal(jobs.docs.committed, false);

  const one = await runMeshJob({
    job: "idle_flavor",
    settings,
    allowNetwork: true,
    events,
    fetchImpl,
    meshPaused: true,
  });
  assert.equal(one.skipped, "mesh-paused");
  assert.equal(one.network, false);
  assert.equal(one.committed, false);
  assert.equal(fetches, 0);
  assert.equal(
    events.all().some((event) => event.type === "idle_flavor" || event.type === "docs_assist"),
    false,
  );
  assert.equal(events.all().some((event) => event.type === "mesh_error"), false);

  paused = false;
  const resumed = await executeMeshJobs({
    settings,
    allowNetwork: true,
    events,
    fetchImpl: async () =>
      jsonResponse(
        { choices: [{ message: { content: "The lamp stays on." } }], model: "qwen2.5:0.5b" },
        { headers: { "X-Pi-Peer": "pi4" } },
      ),
    meshPaused: gate,
    docsHtml: "<title>Meridian Office — Docs</title><p>Keep the list.</p>",
  });
  assert.equal(resumed.skipped, null);
  assert.equal(resumed.idle.committed, true);
  assert.equal(resumed.idle.network, true);
  assert.equal(resumed.idle.text, "The lamp stays on.");
  assert.equal(resumed.docs.committed, true);
  assert.equal(events.all().filter((event) => event.type === "idle_flavor").length, 1);
  assert.equal(events.all().filter((event) => event.type === "docs_assist").length, 1);
});

test("M5 mesh pause is independent of the world kill switch", async () => {
  const root = await tempStudioRoot();
  const docsPath = join(root, "workspace/product/index.html");
  const beforeDocs = await readFile(docsPath, "utf8");
  let fetches = 0;
  const studio = await createStudio({
    root,
    env: testEnv({
      MESH_URL: PAIR,
      MESH_TARGET: "pi2",
      DRY_RUN: "false",
      PORT: "0",
    }),
    listen: true,
    fetchImpl: async (url) => {
      fetches += 1;
      assert.equal(String(url), `${PAIR}/v1/chat/completions`);
      return jsonResponse(
        { choices: [{ message: { content: "The lamp stays on." } }], model: "qwen2.5:0.5b" },
        { headers: { "X-Pi-Peer": "pi4" } },
      );
    },
  });
  try {
    const port = studio.server.server.address().port;

    const open = await studio.snapshot();
    assert.equal(open.paused, false);
    assert.equal(open.mesh.enabled, true);
    assert.equal(open.mesh.paused, false);
    assert.equal(open.mesh.target, "pi2");
    assert.equal(open.mesh.url, PAIR);
    assert.equal(open.mesh.kind, "pair");
    assert.equal(open.mesh.lastPeer, undefined);

    const world = await httpCall(port, "/api/pause", {}, "POST");
    assert.equal(world.status, 200);
    assert.equal(JSON.parse(world.body).paused, true);
    assert.equal(await fileExists(join(root, "data", "PAUSED")), true);
    assert.equal(await fileExists(join(root, "data", "MESH_PAUSED")), false);

    const worldSnap = await studio.snapshot();
    assert.equal(worldSnap.paused, true);
    assert.equal(worldSnap.mesh.paused, false);
    const held = await studio.orchestrator.tickOnce();
    assert.equal(held.skipped, "paused");

    const whileWorldPaused = await studio.runMeshJobs();
    assert.equal(whileWorldPaused.skipped, null);
    assert.equal(whileWorldPaused.idle.committed, true);
    assert.equal(fetches, 2);
    assert.equal((await studio.snapshot()).mesh.lastPeer, "pi4");

    const resumedWorld = await httpCall(port, "/api/resume", {}, "POST");
    assert.equal(resumedWorld.status, 200);
    assert.equal((await studio.snapshot()).paused, false);

    const meshPause = await httpCall(port, "/api/mesh/pause", {}, "POST");
    assert.equal(meshPause.status, 200);
    assert.equal(JSON.parse(meshPause.body).meshPaused, true);
    const pauseFile = JSON.parse(await readFile(join(root, "data", "MESH_PAUSED"), "utf8"));
    assert.equal(pauseFile.paused, true);
    assert.equal(await fileExists(join(root, "data", "PAUSED")), false);

    const meshSnap = await studio.snapshot();
    assert.equal(meshSnap.paused, false);
    assert.equal(meshSnap.mesh.paused, true);
    assert.equal(meshSnap.mesh.enabled, true);
    assert.equal(meshSnap.mesh.target, "pi2");
    assert.equal(meshSnap.mesh.lastPeer, "pi4");

    const health = JSON.parse((await httpCall(port, "/health")).body);
    assert.equal(health.ok, true);
    assert.equal(health.paused, false);
    assert.equal(health.mesh.paused, true);
    assert.equal(health.mesh.enabled, true);
    assert.equal(health.mesh.target, "pi2");

    const beforeFlavor = studio.events.all().filter((event) => event.type === "idle_flavor").length;
    const beforeAssist = studio.events.all().filter((event) => event.type === "docs_assist").length;
    const skipped = await studio.runMeshJobs();
    assert.equal(skipped.skipped, "mesh-paused");
    assert.equal(skipped.idle.network, false);
    assert.equal(fetches, 2);
    assert.equal(studio.events.all().filter((event) => event.type === "idle_flavor").length, beforeFlavor);
    assert.equal(studio.events.all().filter((event) => event.type === "docs_assist").length, beforeAssist);
    assert.equal(await readFile(docsPath, "utf8"), beforeDocs);

    const tick = await studio.orchestrator.tickOnce();
    assert.notEqual(tick.skipped, "paused");
    assert.ok(tick.employee);
    const state = await httpCall(port, "/api/state");
    assert.equal(state.status, 200);
    assert.equal(JSON.parse(state.body).mesh.paused, true);
    assert.equal(JSON.parse(state.body).paused, false);

    const phone = await httpCall(port, "/mesh-pause");
    assert.equal(phone.status, 200);
    assert.match(phone.body, /Mesh paused/);
    assert.match(phone.headers["content-type"], /text\/plain/);

    const meshResume = await httpCall(port, "/api/mesh/resume", {}, "POST");
    assert.equal(meshResume.status, 200);
    assert.equal(JSON.parse(meshResume.body).meshPaused, false);
    assert.equal(await fileExists(join(root, "data", "MESH_PAUSED")), false);
    const again = await studio.runMeshJobs();
    assert.equal(again.skipped, null);
    assert.equal(again.idle.committed, true);
    assert.equal(fetches, 4);
    const after = await studio.snapshot();
    assert.equal(after.paused, false);
    assert.equal(after.mesh.paused, false);
    assert.equal(after.mesh.lastPeer, "pi4");
    const healthLive = JSON.parse((await httpCall(port, "/health")).body);
    assert.equal(healthLive.paused, false);
    assert.equal(healthLive.mesh.paused, false);
  } finally {
    await studio.stop();
  }
});

test("M3 runtime target starts as env MESH_TARGET and the next job follows a phone or API brain switch", async () => {
  const autoRoot = await tempStudioRoot();
  const autoStudio = await createStudio({
    root: autoRoot,
    env: testEnv({ MESH_URL: PAIR }),
    listen: false,
  });
  try {
    const autoSnap = await autoStudio.snapshot();
    assert.equal(autoSnap.mesh.enabled, true);
    assert.equal(autoSnap.mesh.target, "auto");
    assert.equal(autoSnap.mesh.kind, "pair");
    assert.equal(autoSnap.mesh.url, PAIR);
    assert.equal(await fileExists(join(autoRoot, "data", "MESH_TARGET")), false);
  } finally {
    await autoStudio.stop();
  }

  const pinRoot = await tempStudioRoot();
  const pinStudio = await createStudio({
    root: pinRoot,
    env: testEnv({ MESH_URL: PAIR, MESH_TARGET: "pi2" }),
    listen: false,
  });
  try {
    assert.equal((await pinStudio.snapshot()).mesh.target, "pi2");
    assert.equal((await pinStudio.snapshot()).mesh.kind, "pair");
    assert.equal(await fileExists(join(pinRoot, "data", "MESH_TARGET")), false);
  } finally {
    await pinStudio.stop();
  }

  const root = await tempStudioRoot();
  const calls = [];
  const studio = await createStudio({
    root,
    env: testEnv({
      MESH_URL: PAIR,
      MESH_TARGET: "pi4",
      MESH_KIND: "ollama",
      DRY_RUN: "false",
      PORT: "0",
    }),
    listen: true,
    fetchImpl: async (url, init) => {
      calls.push({ url: String(url), init });
      assert.doesNotMatch(String(url), /openrouter\.ai/);
      if (String(url).endsWith("/api/chat")) {
        return jsonResponse({ message: { content: "The lamp stays on." }, model: "qwen2.5:0.5b" });
      }
      return jsonResponse(
        { choices: [{ message: { content: "The lamp stays on." } }], model: "qwen2.5:0.5b" },
        { headers: { "X-Pi-Peer": "pi3" } },
      );
    },
  });
  try {
    const port = studio.server.server.address().port;
    const targetFile = join(root, "data", "MESH_TARGET");

    const booted = await studio.snapshot();
    assert.equal(booted.mesh.target, "pi4");
    assert.equal(booted.mesh.kind, "ollama");
    assert.equal(booted.mesh.url, PAIR);
    assert.equal(booted.mesh.enabled, true);
    assert.equal(booted.paused, false);
    assert.equal(await fileExists(targetFile), false);
    assert.equal(await fileExists(join(root, ".env")), false);
    await expectOllama(port, studio, calls, { target: "pi4" });

    for (const target of ["pi3", "pi2", "pi4", "auto"]) {
      const posted = await httpCall(
        port,
        "/api/mesh/target",
        { "content-type": "application/json" },
        "POST",
        JSON.stringify({ target }),
      );
      assert.equal(posted.status, 200);
      assert.match(posted.headers["content-type"], /application\/json/);
      const body = JSON.parse(posted.body);
      assert.equal(body.target, target);
      assert.equal(body.kind, "pair");
      const saved = JSON.parse(await readFile(targetFile, "utf8"));
      assert.equal(saved.target, target);
      assert.equal(saved.kind, "pair");
      const state = JSON.parse((await httpCall(port, "/api/state")).body);
      assert.equal(state.paused, false);
      assert.equal(state.mesh.paused, false);
      assert.equal(state.mesh.target, target);
      assert.equal(state.mesh.kind, "pair");
      assert.equal(state.mesh.url, PAIR);
      assert.equal(state.mesh.enabled, true);
      const health = JSON.parse((await httpCall(port, "/health")).body);
      assert.equal(health.mesh.target, target);
      assert.equal(health.mesh.kind, "pair");
      assert.equal(health.mesh.url, PAIR);
      await expectPair(port, studio, calls, target);
    }

    const rejected = await httpCall(
      port,
      "/api/mesh/target",
      { "content-type": "application/json" },
      "POST",
      JSON.stringify({ target: "pi5" }),
    );
    assert.equal(rejected.status, 400);
    assert.equal(JSON.parse((await httpCall(port, "/api/state")).body).mesh.target, "auto");
    assert.equal(JSON.parse(await readFile(targetFile, "utf8")).target, "auto");

    for (const pin of ["pi2", "pi3", "pi4"]) {
      const phone = await httpCall(port, `/mesh-pin/${pin}`);
      assert.equal(phone.status, 200);
      assert.match(phone.headers["content-type"], /text\/plain/);
      assert.match(phone.body, new RegExp(pin));
      const saved = JSON.parse(await readFile(targetFile, "utf8"));
      assert.equal(saved.target, pin);
      assert.equal(saved.kind, "pair");
      await expectPair(port, studio, calls, pin);
    }

    const badPin = await httpCall(port, "/mesh-pin/pi5");
    assert.equal(badPin.status, 400);
    assert.match(badPin.headers["content-type"], /text\/plain/);
    assert.equal(JSON.parse(await readFile(targetFile, "utf8")).target, "pi4");

    const apiOllama = await httpCall(
      port,
      "/api/mesh/target",
      { "content-type": "application/json" },
      "POST",
      JSON.stringify({ kind: "ollama" }),
    );
    assert.equal(apiOllama.status, 200);
    assert.equal(JSON.parse(apiOllama.body).kind, "ollama");
    assert.equal(JSON.parse(await readFile(targetFile, "utf8")).kind, "ollama");
    await expectOllama(port, studio, calls, { target: "auto" });

    const phoneOllama = await httpCall(port, "/mesh-ollama");
    assert.equal(phoneOllama.status, 200);
    assert.match(phoneOllama.headers["content-type"], /text\/plain/);
    assert.match(phoneOllama.body, /ollama/);
    assert.equal((await studio.snapshot()).mesh.url, PAIR);
    await expectOllama(port, studio, calls, { target: "auto" });

    const phoneAuto = await httpCall(port, "/mesh-auto");
    assert.equal(phoneAuto.status, 200);
    assert.match(phoneAuto.headers["content-type"], /text\/plain/);
    assert.match(phoneAuto.body, /auto/);
    const autoSaved = JSON.parse(await readFile(targetFile, "utf8"));
    assert.equal(autoSaved.target, "auto");
    assert.equal(autoSaved.kind, "pair");
    await expectPair(port, studio, calls, "auto");
    assert.equal(await fileExists(join(root, ".env")), false);

    const world = await httpCall(port, "/api/pause", {}, "POST");
    assert.equal(world.status, 200);
    assert.equal(JSON.parse(world.body).paused, true);
    assert.equal(await fileExists(join(root, "data", "PAUSED")), true);
    assert.equal(await fileExists(join(root, "data", "MESH_PAUSED")), false);
    const duringWorldPause = await studio.runMeshJobs();
    assert.equal(duringWorldPause.skipped, null);
    assert.equal(duringWorldPause.idle.committed, true);
    assert.equal((await studio.snapshot()).mesh.target, "auto");
    await httpCall(port, "/api/resume", {}, "POST");
    assert.equal(await fileExists(join(root, "data", "PAUSED")), false);

    const meshPause = await httpCall(port, "/api/mesh/pause", {}, "POST");
    assert.equal(meshPause.status, 200);
    assert.equal(JSON.parse(meshPause.body).meshPaused, true);
    const beforePause = calls.length;
    const skipped = await studio.runMeshJobs();
    assert.equal(skipped.skipped, "mesh-paused");
    assert.equal(skipped.idle.network, false);
    assert.equal(calls.length, beforePause);
    const pausedSnap = await studio.snapshot();
    assert.equal(pausedSnap.paused, false);
    assert.equal(pausedSnap.mesh.paused, true);
    assert.equal(pausedSnap.mesh.target, "auto");
    assert.equal(pausedSnap.mesh.kind, "pair");
    assert.equal(await fileExists(join(root, "data", "PAUSED")), false);
    assert.equal(JSON.parse(await readFile(targetFile, "utf8")).target, "auto");

    const phonePause = await httpCall(port, "/mesh-pause");
    assert.equal(phonePause.status, 200);
    assert.match(phonePause.headers["content-type"], /text\/plain/);
    assert.match(phonePause.body, /Mesh paused/);

    const meshResume = await httpCall(port, "/api/mesh/resume", {}, "POST");
    assert.equal(meshResume.status, 200);
    assert.equal(JSON.parse(meshResume.body).meshPaused, false);
    await expectPair(port, studio, calls, "auto");
    const after = await studio.snapshot();
    assert.equal(after.paused, false);
    assert.equal(after.mesh.paused, false);
    assert.equal(after.mesh.target, "auto");
    assert.equal(after.mesh.kind, "pair");
    assert.equal(after.mesh.lastPeer, "pi3");
  } finally {
    await studio.stop();
  }
});

test("M3 office status shows a quiet brain chip from mesh.target and mesh.lastPeer", async () => {
  const html = await readFile(join(REPO, "public/index.html"), "utf8");
  const office = await readFile(join(REPO, "public/office.js"), "utf8");
  const css = await readFile(join(REPO, "public/office.css"), "utf8");

  assert.match(html, /id="status-line"/);
  const statusAt = html.indexOf('id="status-line"');
  const chipAt = html.indexOf('id="brain-chip"');
  const toolsAt = html.indexOf('class="hud-tools"');
  assert.ok(chipAt > statusAt, "brain chip sits in the office status line");
  assert.ok(toolsAt > chipAt, "brain chip stays in the status line");
  const chipLine = html.split("\n").find((line) => line.includes("brain-chip"));
  assert.ok(chipLine);
  assert.doesNotMatch(chipLine, /<button/i);
  assert.doesNotMatch(html, /ticker-track|ON AIR|model-chips|Meridian Desk/);

  assert.match(office, /getElementById\("brain-chip"\)/);
  assert.match(office, /const showBrain = Boolean\(mesh\.enabled\)/);
  assert.match(
    office,
    /mesh\.kind === "ollama" \? "ollama" : mesh\.target === "auto" \? mesh\.lastPeer \|\| "auto" : mesh\.target \|\| "auto"/,
  );
  assert.match(office, /`brain: \$\{brainName\}`/);
  assert.match(office, /classList\.toggle\("hidden", !showBrain\)/);

  const block = css.match(/#brain-chip\s*\{[^}]*\}/);
  assert.ok(block, "quiet brain chip rule");
  assert.match(block[0], /var\(--muted\)/);
  assert.doesNotMatch(block[0], /glow|text-shadow|box-shadow|animation|@keyframes|neon/);
});

test("direct job helper with no URL does not append", async () => {
  const events = await createEventLog({ filePath: join(await tempStudioRoot(), "data", "events.jsonl") });
  const jobs = await executeMeshJobs({
    settings: meshSettingsFromEnv({}),
    allowNetwork: true,
    events,
    fetchImpl: async () => {
      throw new Error("unset mesh must not fetch");
    },
  });
  assert.equal(jobs.skipped, "mesh-unset");
  assert.equal(events.all().length, 0);
});

async function expectPair(port, studio, calls, target) {
  const before = calls.length;
  const jobs = await studio.runMeshJobs();
  assert.equal(jobs.skipped, null);
  assert.equal(jobs.idle.committed, true);
  assert.equal(jobs.idle.network, true);
  assert.equal(jobs.docs.committed, true);
  const batch = calls.slice(before);
  assert.equal(batch.length, 2);
  for (const call of batch) {
    assert.equal(call.url, `${PAIR}/v1/chat/completions`);
    assert.equal(call.init.headers["X-Pi-Target"], target);
    assert.equal(call.init.headers["X-Pi-Mesh"], "on");
    const payload = JSON.parse(call.init.body);
    assert.equal(payload.pi_target, target);
    assert.equal(payload.pi_mesh, "on");
  }
  const state = JSON.parse((await httpCall(port, "/api/state")).body);
  assert.equal(state.mesh.target, target);
  assert.equal(state.mesh.kind, "pair");
  assert.equal(state.mesh.url, PAIR);
  const health = JSON.parse((await httpCall(port, "/health")).body);
  assert.equal(health.mesh.target, target);
  assert.equal(health.mesh.kind, "pair");
  assert.equal(health.mesh.url, PAIR);
}

async function expectOllama(port, studio, calls, { target } = {}) {
  const before = calls.length;
  const jobs = await studio.runMeshJobs();
  assert.equal(jobs.skipped, null);
  assert.equal(jobs.idle.committed, true);
  assert.equal(jobs.docs.committed, true);
  const batch = calls.slice(before);
  assert.equal(batch.length, 2);
  for (const call of batch) {
    assert.equal(call.url, `${PAIR}/api/chat`);
    assert.equal(call.init.headers["X-Pi-Target"], undefined);
    const payload = JSON.parse(call.init.body);
    assert.equal(payload.pi_target, undefined);
    assert.equal(payload.stream, false);
  }
  const state = JSON.parse((await httpCall(port, "/api/state")).body);
  assert.equal(state.mesh.kind, "ollama");
  assert.equal(state.mesh.url, PAIR);
  assert.equal(state.mesh.enabled, true);
  if (target) assert.equal(state.mesh.target, target);
  const health = JSON.parse((await httpCall(port, "/health")).body);
  assert.equal(health.mesh.kind, "ollama");
  assert.equal(health.mesh.url, PAIR);
  if (target) assert.equal(health.mesh.target, target);
}

async function fileExists(path) {
  try {
    await access(path);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

function httpCall(port, path, headers = {}, method = "GET", body) {
  const payload = body == null ? null : Buffer.from(body);
  const hdrs = { ...headers };
  if (payload) hdrs["content-length"] = String(payload.length);
  return new Promise((resolve, reject) => {
    const req = request({ hostname: "127.0.0.1", port, path, method, headers: hdrs }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        resolve({
          status: res.statusCode,
          headers: res.headers,
          body: Buffer.concat(chunks).toString("utf8"),
        });
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}
