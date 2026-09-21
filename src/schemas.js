export const KNOWN_ITEMS = [
  "monitor",
  "second_monitor",
  "plant",
  "coffee_mug",
  "notebook",
  "sticky_notes",
];

export const KNOWN_DECOR = [
  "whiteboard",
  "coffee",
  "couch",
  "plant",
  "rug",
  "lamp",
  "shelf",
  "clock",
  "standing_desk",
  "filing_cabinet",
  "beanbag",
  "minifridge",
  "table",
];

export function validateOffice(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "office.json must be an object";
  }
  if (typeof value.walls !== "string" || typeof value.floor !== "string") {
    return "office.json needs walls and floor strings";
  }
  if (!Array.isArray(value.desks) || value.desks.length === 0) {
    return "office.json needs at least one desk";
  }
  for (const desk of value.desks) {
    if (!desk || typeof desk.owner !== "string") return "desk.owner required";
    if (!Number.isFinite(desk.x) || !Number.isFinite(desk.y)) return "desk x/y required";
    if (!Array.isArray(desk.items) || desk.items.length === 0) {
      return `desk ${desk.owner} needs items (monitor, plant, mug…)`;
    }
  }
  if (!Array.isArray(value.rooms)) return "office.json.rooms must be an array";
  const hasBreak = value.rooms.some((room) => /break/i.test(room?.name || ""));
  if (!hasBreak) return "office.json needs a break room";
  if (!Array.isArray(value.decor)) return "office.json.decor must be an array";
  if (!value.decor.some((item) => item?.kind === "whiteboard")) {
    return "office.json needs a whiteboard";
  }
  if (!value.decor.some((item) => item?.kind === "coffee")) {
    return "office.json needs a coffee machine";
  }
  if (value.budget && !Number.isFinite(Number(value.budget.furniture))) {
    return "office.json.budget.furniture must be a number";
  }
  for (const item of value.decor) {
    if (item?.kind && !KNOWN_DECOR.includes(item.kind)) {
      return `unknown decor kind ${item.kind}`;
    }
  }
  return null;
}

export function validateBacklog(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "backlog.json must be an object";
  }
  if (!Array.isArray(value.tasks)) return "backlog.json.tasks must be an array";
  for (const task of value.tasks) {
    if (!task || typeof task.id !== "string" || typeof task.text !== "string") {
      return "each task needs id and text";
    }
    if (task.status !== "open" && task.status !== "closed") {
      return "task.status must be open or closed";
    }
  }
  return null;
}

export function emptyBacklog() {
  return { tasks: [] };
}
