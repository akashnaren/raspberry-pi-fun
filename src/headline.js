const FALLBACK = {
  mira: "Mira Sol",
  nova: "Nova Chen",
  kessler: "Kessler Holt",
  jules: "Jules Park",
  system: "Meridian Desk",
};

export function displayName(id, names = {}) {
  return names[id] || FALLBACK[id] || id;
}

export function firstName(id, names = {}) {
  return displayName(id, names).split(" ")[0];
}

export function prettyPath(path) {
  const rel = String(path || "").replace(/^\/+/, "");
  if (rel === "product/index.html") return "Timezone Buddy";
  if (rel.startsWith("product/")) return "the working copy";
  if (rel === "backlog.json") return "the backlog";
  if (rel === "office.json") return "the office layout";
  if (rel === "relationships.json") return "the relationship matrix";
  if (rel === "requests.json") return "the request pile";
  if (/^employees\/[^/]+\.json$/.test(rel)) return "their look";
  if (rel.endsWith("/journal.md")) return "their journal";
  return rel || "a file";
}

function clip(text, max = 80) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  return `${clean.slice(0, max - 1)}…`;
}

export function headlineFor(event, names = {}) {
  const who = displayName(event.actor, names);
  const nova = firstName("nova", names);
  const kessler = firstName("kessler", names);
  const data = event.data || {};
  switch (event.type) {
    case "world_started":
      return data.dryRun
        ? "Meridian Desk opened in dry-run. The lights are on; nobody is spending."
        : "Meridian Desk went live. The clock is running.";
    case "world_paused":
      return "Meridian Desk paused. The room holds still.";
    case "world_resumed":
      return "Meridian Desk resumed. Someone will move.";
    case "budget_paused":
      return "Studio sleeping. Daily ceiling hit; lights down until tomorrow.";
    case "turn_started":
      return `${who} is acting.`;
    case "turn_finished":
      return `${who} finished a turn.`;
    case "turn_failed":
      return `${who}'s turn stumbled.`;
    case "say":
      return `${who} said, “${clip(data.text, 72)}”`;
    case "journal":
      return `${who} wrote in their journal.`;
    case "file_read":
      return `${who} read ${prettyPath(data.path)}.`;
    case "file_written":
      return `${who} wrote ${prettyPath(data.path)}.`;
    case "task_added":
      return `${who} filed a task: ${clip(data.task?.text || data.text, 64)}`;
    case "task_closed":
      return `${who} closed a task.`;
    case "build_failed":
      if (event.actor === "kessler") return `${kessler} rejected ${nova}'s write.`;
      return `${who}'s write did not go green.`;
    case "build_passed":
      return data.stage === "seed"
        ? "Timezone Buddy is on the right pane."
        : `${who} shipped a green build to the floor.`;
    case "model_resolved":
      return `${who} is provisioned on ${data.model || "a hosted model"}.`;
    case "loop_broken":
      return `${who} broke a repeating loop.`;
    case "tool_error":
      return `${who} could not ${data.tool || "finish that"}.`;
    case "request_filed":
      return `${who} asked Jules for ${clip(data.item, 40)}.`;
    case "request_decided":
      return `${who} ${data.decision || "answered"} a request for ${clip(data.item, 40)}.`;
    case "office_edited":
      return `${who} moved the furniture.`;
    case "office_edit_rejected":
      return `${who}'s office change was refused: ${clip(data.error, 48)}`;
    case "aesthetics_changed":
      return `${who} changed clothes.`;
    default:
      return event.message || `${who} did something.`;
  }
}
