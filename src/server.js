import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, relative, resolve } from "node:path";
import { WebSocketServer } from "ws";

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
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
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
    if (url.pathname === "/kill") {
      await killSwitch.pause("phone");
      res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
      res.end("Fishbowl paused. Open / or POST /api/resume to start again.\n");
      return;
    }
    if (url.pathname === "/health") {
      const snap = budget.snapshot();
      return json(res, { ok: true, paused: await killSwitch.paused(), budget: snap });
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
