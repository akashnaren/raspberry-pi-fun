/**
 * Pi-PAIR / Ollama chat client for Meridian mesh jobs.
 * Workers return text. The stage commits events. No OpenRouter fallback.
 */

export const MESH_TARGETS = Object.freeze(["auto", "pi2", "pi3", "pi4"]);
export const DEFAULT_MESH_TARGET = "auto";
export const DEFAULT_MESH_MODEL = "qwen2.5:0.5b";
export const DEFAULT_MESH_TIMEOUT_MS = 45_000;

export class MeshError extends Error {
  constructor(message, { code = "mesh_error", target = "", status = 0 } = {}) {
    super(message);
    this.name = "MeshError";
    this.code = code;
    this.target = target;
    this.status = status;
  }
}

export function isPinnedTarget(target) {
  return target === "pi2" || target === "pi3" || target === "pi4";
}

export function normalizeMeshTarget(value) {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw) return DEFAULT_MESH_TARGET;
  if (!MESH_TARGETS.includes(raw)) {
    throw new MeshError(`MESH_TARGET must be auto, pi2, pi3, or pi4 (got ${raw})`, {
      code: "bad_target",
    });
  }
  return raw;
}

export function parseMeshUrl(url) {
  let parsed;
  try {
    parsed = new URL(String(url || "").trim());
  } catch {
    throw new MeshError("MESH_URL must be an absolute http(s) URL", { code: "bad_url" });
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new MeshError("MESH_URL must be an absolute http(s) URL", { code: "bad_url" });
  }
  return parsed;
}

/** Port 18080 is Pi-PAIR. Port 11434 is Ollama. MESH_KIND overrides the port. */
export function detectMeshKind(url, explicit) {
  const forced = String(explicit ?? "").trim().toLowerCase();
  if (forced === "pair" || forced === "ollama") return forced;
  if (forced) {
    throw new MeshError("MESH_KIND must be pair or ollama", { code: "bad_kind" });
  }
  const port = parseMeshUrl(url).port;
  if (port === "11434") return "ollama";
  if (port === "18080") return "pair";
  return "pair";
}

export function meshSettingsFromEnv(env = process.env) {
  const url = String(env.MESH_URL ?? "").trim();
  const target = normalizeMeshTarget(env.MESH_TARGET);
  const timeoutMs = positiveInt(env.MESH_TIMEOUT_MS, DEFAULT_MESH_TIMEOUT_MS);
  const jobMs = Math.max(30_000, positiveInt(env.MESH_JOB_MS, 600_000));
  const model = String(env.MESH_MODEL ?? "").trim() || DEFAULT_MESH_MODEL;
  if (!url) {
    return {
      url: "",
      enabled: false,
      target,
      kind: "pair",
      model,
      timeoutMs,
      jobMs,
    };
  }
  const parsed = parseMeshUrl(url);
  return {
    url: parsed.origin,
    enabled: true,
    target,
    kind: detectMeshKind(parsed.origin, env.MESH_KIND),
    model,
    timeoutMs,
    jobMs,
  };
}

/**
 * DRY_RUN and replay stay off the network. DRY_RUN=false calls the mesh
 * even when OpenRouter has no key — local inference is not billed there.
 */
export function meshNetworkAllowed(env = process.env) {
  if (env.STUDIO_MODE === "replay" || env.REPLAY === "true" || env.REPLAY === "1") return false;
  return env.DRY_RUN === "false" || env.DRY_RUN === "0";
}

export function buildMeshChatRequest(settings, { messages, model } = {}) {
  if (!settings?.url) {
    throw new MeshError("MESH_URL is unset", { code: "mesh_unset" });
  }
  const origin = parseMeshUrl(settings.url).origin;
  const target = normalizeMeshTarget(settings.target);
  const kind = settings.kind === "ollama" ? "ollama" : "pair";
  const useModel = model || settings.model || DEFAULT_MESH_MODEL;
  if (kind === "ollama") {
    return {
      url: `${origin}/api/chat`,
      kind,
      headers: { "content-type": "application/json" },
      body: {
        model: useModel,
        messages: messages || [],
        stream: false,
      },
    };
  }
  return {
    url: `${origin}/v1/chat/completions`,
    kind,
    headers: {
      "content-type": "application/json",
      "X-Pi-Target": target,
      "X-Pi-Mesh": "on",
    },
    body: {
      model: useModel,
      messages: messages || [],
      stream: false,
      pi_target: target,
      pi_mesh: "on",
    },
  };
}

export function readMeshText(kind, body) {
  if (kind === "ollama") {
    return String(body?.message?.content || body?.response || "").trim();
  }
  const content = body?.choices?.[0]?.message?.content;
  return String(content || "").trim();
}

export async function meshChat({
  settings,
  messages,
  fetchImpl = fetch,
  timeoutMs,
  model,
} = {}) {
  if (!settings?.enabled || !settings?.url) {
    throw new MeshError("MESH_URL is unset", { code: "mesh_unset", target: settings?.target || "" });
  }
  const req = buildMeshChatRequest(settings, { messages, model });
  const ms = positiveInt(timeoutMs, settings.timeoutMs || DEFAULT_MESH_TIMEOUT_MS);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  let response;
  try {
    response = await fetchImpl(req.url, {
      method: "POST",
      headers: req.headers,
      body: JSON.stringify(req.body),
      signal: controller.signal,
    });
  } catch (error) {
    const aborted = error?.name === "AbortError" || controller.signal.aborted;
    throw meshFailure(settings, aborted ? "timeout" : error?.message || "mesh unreachable", {
      aborted,
    });
  } finally {
    clearTimeout(timer);
  }
  const raw = await response.text();
  let body = {};
  try {
    body = raw ? JSON.parse(raw) : {};
  } catch {
    body = { error: raw };
  }
  if (!response.ok) {
    const detail = errorDetail(body, raw);
    throw meshFailure(settings, detail, { status: response.status });
  }
  const text = readMeshText(req.kind, body);
  if (!text) {
    throw new MeshError("mesh returned an empty reply", {
      code: "empty",
      target: settings.target,
    });
  }
  const peer = headerValue(response, "x-pi-peer");
  return {
    text,
    peer,
    host: headerValue(response, "x-pi-host"),
    model: body.model || req.body.model,
    target: settings.target,
    kind: req.kind,
  };
}

function meshFailure(settings, detail, { status = 0, aborted = false } = {}) {
  const target = settings?.target || "";
  const text = String(detail || "").trim();
  const named = text.match(/\b(pi[234])\s+offline\b/i);
  const looksOffline =
    Boolean(named) ||
    /offline/i.test(text) ||
    aborted ||
    status === 502 ||
    status === 503 ||
    status === 504 ||
    /timeout|econnrefused|enotfound|fetch failed|network|socket/i.test(text);
  if (isPinnedTarget(target) && looksOffline) {
    return new MeshError(named ? `${named[1].toLowerCase()} offline` : `${target} offline`, {
      code: "peer_offline",
      target,
      status,
    });
  }
  if (named) {
    return new MeshError(`${named[1].toLowerCase()} offline`, {
      code: "peer_offline",
      target,
      status,
    });
  }
  if (aborted) {
    return new MeshError("mesh timeout", { code: "timeout", target, status });
  }
  return new MeshError(text.slice(0, 240) || "mesh request failed", {
    code: status ? "http" : "network",
    target,
    status,
  });
}

function errorDetail(body, raw) {
  const err = body?.error;
  if (typeof err === "string") return err;
  if (err && typeof err.message === "string") return err.message;
  if (typeof body?.message === "string") return body.message;
  return String(raw || "").slice(0, 240);
}

function headerValue(response, name) {
  const headers = response?.headers;
  if (!headers) return "";
  if (typeof headers.get === "function") return String(headers.get(name) || "");
  const found = Object.entries(headers).find(([key]) => key.toLowerCase() === name.toLowerCase());
  return found ? String(found[1] || "") : "";
}

function positiveInt(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : fallback;
}
