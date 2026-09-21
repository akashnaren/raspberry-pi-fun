import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const vocab = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "props-vocab.json"), "utf8"),
);

export const PROP_VOCAB = vocab.props;
export const PROP_IDS = new Set(PROP_VOCAB.map((item) => item.id));

export const KIND_ALIASES = {
  coffee: "coffee_machine",
  couch: "couch_2seat",
  table: "table_round",
  plant: "plant_small",
  lamp: "lamp_desk",
  clock: "clock_wall",
  shelf: "bookshelf",
  rug: "rug_round",
  coffee_mug: "mug",
  sticky_notes: "sticky_stack",
};

export const KNOWN_ITEMS = [
  "monitor",
  "keyboard",
  "laptop",
  "mouse",
  "mug",
  "plant_small",
  "lamp_desk",
  "notebook",
  "sticky_stack",
  "headphones",
  "phone",
  "nameplate_stand",
  "chair",
];

export const KNOWN_DECOR = [
  "whiteboard",
  "coffee",
  "coffee_machine",
  "couch",
  "couch_2seat",
  "plant",
  "plant_small",
  "plant_tall",
  "rug",
  "rug_round",
  "lamp",
  "lamp_desk",
  "lamp_floor",
  "shelf",
  "bookshelf",
  "clock",
  "clock_wall",
  "filing_cabinet",
  "table",
  "table_round",
  "meeting_table",
  "water_cooler",
  "break_snack",
  "poster_ship",
  "divider_low",
  "frame_art",
  "crate_storage",
  "plant_pot",
  "trash_bin",
];

function deskOwner(desk) {
  return desk?.owner || desk?.employee;
}

function deskItems(desk) {
  if (Array.isArray(desk?.items) && desk.items.length) return desk.items;
  return (desk?.props || []).map((item) => item.id).filter(Boolean);
}

function resolvePropId(id) {
  if (!id) return null;
  if (PROP_IDS.has(id)) return id;
  return KIND_ALIASES[id] || null;
}

export function validateOffice(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "office.json must be an object";
  }
  const norm = value.coord_space === "topdown_norm";
  if (!norm && (typeof value.walls !== "string" || typeof value.floor !== "string")) {
    return "office.json needs walls and floor strings";
  }
  if (!Array.isArray(value.desks) || value.desks.length === 0) {
    return "office.json needs at least one desk";
  }
  for (const desk of value.desks) {
    const owner = deskOwner(desk);
    if (!owner) return "desk.owner required";
    if (!Number.isFinite(desk.x) || !Number.isFinite(desk.y)) return "desk x/y required";
    const items = deskItems(desk);
    if (!items.length) return `desk ${owner} needs items (monitor, plant, mug…)`;
    for (const item of items) {
      const resolved = resolvePropId(item);
      if (!resolved || !PROP_IDS.has(resolved)) return `unknown desk prop ${item}`;
    }
  }
  const rooms = value.rooms || [];
  const zones = value.zones || [];
  const hasBreak =
    rooms.some((room) => /break/i.test(room?.name || room?.id || "")) ||
    zones.some((zone) => /break/i.test(zone?.id || zone?.prop || ""));
  if (!hasBreak) return "office.json needs a break room";
  if (!norm && !Array.isArray(value.rooms)) return "office.json.rooms must be an array";
  const decor = value.decor || [];
  const objects = value.objects || [];
  const surface = decor.length ? decor : objects;
  if (!surface.length) return "office.json.decor must be an array";
  const hasBoard = surface.some((item) => item?.kind === "whiteboard" || item?.prop === "whiteboard");
  if (!hasBoard) return "office.json needs a whiteboard";
  const hasCoffee = surface.some(
    (item) => item?.kind === "coffee" || item?.prop === "coffee_machine" || item?.prop === "coffee",
  );
  if (!hasCoffee) return "office.json needs a coffee machine";
  if (value.budget && !Number.isFinite(Number(value.budget.furniture))) {
    return "office.json.budget.furniture must be a number";
  }
  for (const item of surface) {
    const id = resolvePropId(item?.prop || item?.kind);
    if (item?.kind || item?.prop) {
      if (!id || !PROP_IDS.has(id)) return `unknown decor kind ${item.prop || item.kind}`;
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
