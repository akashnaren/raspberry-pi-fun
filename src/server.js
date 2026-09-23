import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, relative, resolve } from "node:path";
import { WebSocketServer } from "ws";

/** Public host a tunnel viewer used. Path routing never depends on it. */
export function viewerOrigin(req) {
  const forwardedHost = firstHeaderToken(req?.headers?.["x-forwarded-host"]);
  const host = forwardedHost || safeHost(req?.headers?.host) || "localhost";
  const forwardedProto = firstHeaderToken(req?.headers?.["x-forwarded-proto"]);
  const proto = forwardedProto === "https" || forwardedProto === "http" ? forwardedProto : "http";
  return `${proto}://${host}`;
}

function firstHeaderToken(value) {
  const raw = Array.isArray(value) ? value[0] : value;
  if (!raw) return "";
  return safeHost(String(raw).split(",")[0]);
}

function safeHost(value) {
  const host = String(value || "").trim();
  if (!host || /[\s/\\]/.test(host)) return "";
  if (!/^[A-Za-z0-9.\-:[\]]+$/.test(host)) return "";
  return host;
}

function requestUrl(req) {
  const host = safeHost(req.headers?.host) || "localhost";
  try {
    return new URL(req.url || "/", `http://${host}`);
  } catch {
    return new URL(req.url || "/", "http://localhost");
  }
}

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".txt": "text/plain; charset=utf-8",
};

export function createStudioServer({
  host,
  port,
  publicDir,
  workspaceRoot,
  distRoot,
  events,
  budget,
  killSwitch,
  meshPause,
  meshTarget,
  meshStatus,
  getSnapshot,
  pendingValidations,
}) {
  const server = createServer(async (req, res) => {
    try {
      await handle(req, res);
    } catch (error) {
      res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
      res.end(error.message);
    }
  });
  // Stay above a reverse tunnel's idle timeout so upgrades are not cut early.
  server.keepAliveTimeout = 65_000;
  server.headersTimeout = 66_000;

  const wss = new WebSocketServer({ server, path: "/ws" });
  wss.on("connection", (socket) => {
    socket.send(JSON.stringify({ type: "hello", state: getSnapshot() }));
    socket.on("message", (raw) => {
      let msg;
      try {
        msg = JSON.parse(String(raw));
      } catch {
        return;
      }
      if (msg.type === "validation_result" && msg.id && pendingValidations.has(msg.id)) {
        const pending = pendingValidations.get(msg.id);
        pendingValidations.delete(msg.id);
        pending.resolve({
          ok: Boolean(msg.ok),
          error: msg.error || "",
        });
      }
    });
  });

  const unsubscribeEvents = events.subscribe((event) => {
    broadcast({ type: "event", event, state: getSnapshot() });
  });

  function broadcast(payload) {
    const data = JSON.stringify(payload);
    for (const client of wss.clients) {
      if (client.readyState === 1) client.send(data);
    }
  }

  async function handle(req, res) {
    const url = requestUrl(req);
    if (url.pathname === "/api/state") {
      return json(res, getSnapshot());
    }
    if (url.pathname === "/api/pause" && req.method === "POST") {
      await killSwitch.pause("api");
      return json(res, { paused: true });
    }
    if (url.pathname === "/api/resume" && req.method === "POST") {
      await killSwitch.resume();
      await events.append({
        type: "world_resumed",
        actor: "system",
        message: "world resumed",
      });
      return json(res, { paused: false });
    }
    if (url.pathname === "/api/mesh/pause" && req.method === "POST") {
      await meshPause.pause("api");
      return json(res, { meshPaused: true });
    }
    if (url.pathname === "/api/mesh/resume" && req.method === "POST") {
      await meshPause.resume();
      return json(res, { meshPaused: false });
    }
    if (url.pathname === "/kill") {
      await killSwitch.pause("phone");
      res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
      res.end("Office paused. Open / or POST /api/resume to start again.\n");
      return;
    }
    if (url.pathname === "/mesh-pause") {
      await meshPause.pause("phone");
      res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
      res.end(
        "Mesh paused. Idle flavor and Docs assist stop. The office keeps running. POST /api/mesh/resume to start mesh jobs again.\n",
      );
      return;
    }
    if (url.pathname === "/api/mesh/target" && req.method === "POST") {
      let body;
      try {
        body = await readJsonBody(req);
      } catch (error) {
        if (error?.code === "bad_body") return meshTargetError(res, error.message);
        throw error;
      }
      try {
        const settings =
          body.kind === "ollama"
            ? await meshTarget.setOllama("api")
            : await meshTarget.setPairTarget(requireMeshTarget(body.target), "api");
        return json(res, { target: settings.target, kind: settings.kind });
      } catch (error) {
        if (error?.code === "bad_target") return meshTargetError(res, error.message);
        throw error;
      }
    }
    if (url.pathname === "/mesh-auto") {
      const settings = await meshTarget.setPairTarget("auto", "phone");
      return plain(res, 200, `Mesh auto. Next job sends X-Pi-Target ${settings.target}.\n`);
    }
    if (url.pathname === "/mesh-ollama") {
      await meshTarget.setOllama("phone");
      return plain(
        res,
        200,
        "Mesh ollama. Next job uses /api/chat on the configured MESH_URL.\n",
      );
    }
    if (url.pathname.startsWith("/mesh-pin/")) {
      const pin = url.pathname.slice("/mesh-pin/".length);
      if (pin !== "pi2" && pin !== "pi3" && pin !== "pi4") {
        return plain(res, 400, "Mesh pin must be pi2, pi3, or pi4.\n");
      }
      const settings = await meshTarget.setPairTarget(pin, "phone");
      return plain(res, 200, `Mesh pin ${settings.target}. Next job sends X-Pi-Target ${settings.target}.\n`);
    }
    if (url.pathname === "/health") {
      const snap = budget.snapshot();
      const body = {
        ok: true,
        paused: await killSwitch.paused(),
        budget: snap,
        origin: viewerOrigin(req),
      };
      if (meshStatus) body.mesh = await meshStatus();
      return json(res, body);
    }

    if (url.pathname === "/" || url.pathname === "/index.html") {
      return sendFile(res, join(publicDir, "index.html"));
    }
    if (url.pathname.startsWith("/dist/")) {
      return sendSafe(res, distRoot, url.pathname.slice("/dist/".length));
    }
    if (url.pathname === "/dist") {
      res.writeHead(302, { location: "/dist/" });
      res.end();
      return;
    }
    if (url.pathname.startsWith("/candidate/")) {
      return sendSafe(res, join(workspaceRoot, "product"), url.pathname.slice("/candidate/".length));
    }
    return sendSafe(res, publicDir, url.pathname.slice(1));
  }

  return {
    server,
    wss,
    broadcast,
    async listen() {
      await new Promise((resolveListen) => {
        server.listen(port, host, resolveListen);
      });
      return { host, port };
    },
    async close() {
      unsubscribeEvents();
      await new Promise((resolveClose) => wss.close(() => server.close(resolveClose)));
    },
  };
}

async function sendSafe(res, root, rel) {
  const target = resolve(root, normalize(rel || "index.html"));
  const relToRoot = relative(root, target);
  if (relToRoot.startsWith("..")) {
    res.writeHead(403);
    res.end("forbidden");
    return;
  }
  let file = target;
  try {
    const info = await stat(file);
    if (info.isDirectory()) file = join(file, "index.html");
  } catch {
    res.writeHead(404);
    res.end("not found");
    return;
  }
  return sendFile(res, file);
}

async function sendFile(res, file) {
  const body = await readFile(file);
  res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" });
  res.end(body);
}

function json(res, body) {
  res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
  res.end(`${JSON.stringify(body)}\n`);
}

function plain(res, status, message) {
  res.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
  res.end(message);
}

function meshTargetError(res, message) {
  res.writeHead(400, { "content-type": "application/json; charset=utf-8" });
  res.end(`${JSON.stringify({ error: message })}\n`);
}

function requireMeshTarget(value) {
  const raw = String(value ?? "").trim();
  if (!raw) {
    const error = new Error("MESH_TARGET must be auto, pi2, pi3, or pi4");
    error.code = "bad_target";
    throw error;
  }
  return raw;
}

async function readJsonBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    const error = new Error("JSON body required");
    error.code = "bad_body";
    throw error;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    const error = new Error("JSON object required");
    error.code = "bad_body";
    throw error;
  }
  return parsed;
}
