/**
 * Idle flavor + Docs assist. Both write only through the stage event log.
 * DRY_RUN skips the network and does not append (scripted evening stays $0).
 * Mesh pause skips the network and does not append. The office stage keeps running.
 */

import { MeshError, meshChat } from "./mesh.js";

export const IDLE_FLAVOR_STUB = "The room is still. Coffee waits on the counter.";
export const DOCS_ASSIST_STUB = "Docs: a short heading, then the list.";

const JOBS = {
  idle_flavor: {
    stub: IDLE_FLAVOR_STUB,
    prompt:
      "Write one short ambient line for a quiet office. Plain English. No markup. Under 80 characters. Do not mention models or APIs.",
  },
  docs_assist: {
    stub: DOCS_ASSIST_STUB,
    prompt(excerpt) {
      return `Write one short Docs assist line for Meridian Office. Plain English. No markup. Under 90 characters. Do not offer to rewrite the file. Excerpt: ${excerpt}`;
    },
  },
};

export function meshJobStub(job) {
  return JOBS[job]?.stub || "";
}

export function clipMeshLine(text, max = 90) {
  const clean = String(text || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!clean) return "";
  const sentence = clean.split(/(?<=[.!?])\s/)[0] || clean;
  if (sentence.length <= max) return sentence;
  return `${sentence.slice(0, max - 1).trim()}…`;
}

/** Local excerpt only. The worker never receives a file write. */
export function docsExcerpt(html, max = 220) {
  const src = String(html || "");
  const title = src.match(/<title>([^<]*)<\/title>/i)?.[1]?.replace(/\s+/g, " ").trim() || "Meridian Office";
  const body = src
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const excerpt = body.slice(0, max);
  return excerpt ? `${title}. ${excerpt}` : title;
}

export function meshJobMessages(job, { docsHtml = "" } = {}) {
  const spec = JOBS[job];
  if (!spec) throw new MeshError(`unknown mesh job ${job}`, { code: "bad_job" });
  const content = job === "docs_assist" ? spec.prompt(docsExcerpt(docsHtml)) : spec.prompt;
  return [
    {
      role: "system",
      content: "You write a single display line for one Meridian office. No tools. No files.",
    },
    { role: "user", content },
  ];
}

/**
 * One job. Unset MESH_URL returns skipped and does not touch the log.
 * DRY_RUN (allowNetwork false) returns the stub and does not fetch or append.
 * Mesh pause returns skipped mesh-paused and does not fetch or append.
 * Live mesh text is appended on the stage. Failures append mesh_error and do not call OpenRouter.
 */
export async function runMeshJob({
  job,
  settings,
  allowNetwork = false,
  events,
  fetchImpl,
  docsHtml = "",
  meshPaused = false,
} = {}) {
  if (await readMeshPaused(meshPaused)) return pausedJob(job);
  if (!settings?.enabled) {
    return { job, skipped: "mesh-unset", committed: false, network: false, costUsd: 0 };
  }
  if (!JOBS[job]) throw new MeshError(`unknown mesh job ${job}`, { code: "bad_job" });
  if (!allowNetwork) {
    return {
      job,
      skipped: "dry-run",
      via: "stub",
      text: meshJobStub(job),
      committed: false,
      network: false,
      costUsd: 0,
    };
  }
  try {
    const result = await meshChat({
      settings,
      messages: meshJobMessages(job, { docsHtml }),
      fetchImpl,
    });
    const text = clipMeshLine(result.text);
    if (!text) {
      throw new MeshError("mesh returned an empty reply", {
        code: "empty",
        target: settings.target,
      });
    }
    const event = await commitLine(events, {
      type: job,
      text,
      data: {
        via: "mesh",
        authority: "stage",
        target: settings.target,
        kind: result.kind,
        peer: result.peer || "",
        model: result.model || settings.model,
      },
    });
    return {
      job,
      skipped: null,
      via: "mesh",
      text,
      committed: true,
      network: true,
      costUsd: 0,
      event,
    };
  } catch (error) {
    const message = error?.message || "mesh request failed";
    const event = events
      ? await commitLine(events, {
          type: "mesh_error",
          text: message,
          data: {
            via: "mesh",
            authority: "stage",
            job,
            code: error?.code || "mesh_error",
            target: settings.target || "",
          },
        })
      : null;
    return {
      job,
      skipped: null,
      via: "mesh",
      error: {
        message,
        code: error?.code || "mesh_error",
        target: settings.target || "",
      },
      committed: Boolean(event),
      network: true,
      costUsd: 0,
      event,
    };
  }
}

export async function executeMeshJobs(opts = {}) {
  if (await readMeshPaused(opts.meshPaused)) {
    return {
      skipped: "mesh-paused",
      idle: pausedJob("idle_flavor"),
      docs: pausedJob("docs_assist"),
    };
  }
  if (!opts.settings?.enabled) {
    return { skipped: "mesh-unset", idle: null, docs: null };
  }
  const idle = await runMeshJob({ ...opts, job: "idle_flavor" });
  const docs = await runMeshJob({ ...opts, job: "docs_assist" });
  return { skipped: null, idle, docs };
}

async function readMeshPaused(meshPaused) {
  if (typeof meshPaused === "function") return Boolean(await meshPaused());
  return Boolean(meshPaused);
}

function pausedJob(job) {
  return {
    job,
    skipped: "mesh-paused",
    committed: false,
    network: false,
    costUsd: 0,
  };
}

async function commitLine(events, { type, text, data }) {
  if (!events?.append) throw new MeshError("stage event log is required", { code: "no_authority" });
  return events.append({
    type,
    actor: "system",
    message: text,
    data: { text, ...data },
  });
}
