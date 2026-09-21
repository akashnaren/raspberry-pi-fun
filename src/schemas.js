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
  "window",
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

const TWEAK_ONLY = (args) =>
  Boolean(args && (args.move || args.whiteboard || args.deskItem || args.add));

export function isOfficeTweak(args) {
  if (!TWEAK_ONLY(args)) return false;
  if (args.office || args.desks || args.decor || args.rooms || args.walls) return false;
  return true;
}

/**
 * Small Jules patches: move a prop, rewrite the board, drop a desk item, or add one decor.
 * Full office.json replacements still go through edit_office({ office }).
 */
export function applyOfficeTweak(office, tweak) {
  if (!office || !tweak || typeof tweak !== "object") {
    return { office, changes: [] };
  }
  const next = {
    ...office,
    desks: (office.desks || []).map((desk) => ({ ...desk, items: [...(desk.items || [])] })),
    rooms: (office.rooms || []).map((room) => ({ ...room })),
    decor: (office.decor || []).map((item) => ({ ...item })),
    budget: { ...(office.budget || {}) },
  };
  const changes = [];

  if (typeof tweak.whiteboard === "string" && tweak.whiteboard.trim()) {
    const board = next.decor.find((item) => item.kind === "whiteboard");
    if (board) {
      const text = tweak.whiteboard.trim().slice(0, 96);
      if (board.text !== text) {
        board.text = text;
        changes.push({ action: "whiteboard", kind: "whiteboard", x: board.x, y: board.y, at: "whiteboard" });
      }
    }
  }

  if (tweak.move && typeof tweak.move === "object") {
    const kind = String(tweak.move.kind || "");
    const destX = Number(tweak.move.x ?? tweak.move.to?.x);
    const destY = Number(tweak.move.y ?? tweak.move.to?.y);
    if (kind && Number.isFinite(destX) && Number.isFinite(destY)) {
      const from = tweak.move.from;
      let item = null;
      if (from && Number.isFinite(Number(from.x)) && Number.isFinite(Number(from.y))) {
        item = next.decor.find((entry) => entry.kind === kind && entry.x === from.x && entry.y === from.y);
      }
      if (!item) item = next.decor.find((entry) => entry.kind === kind && entry.x === destX && entry.y === destY);
      if (!item) item = next.decor.find((entry) => entry.kind === kind);
      if (item && (item.x !== destX || item.y !== destY)) {
        item.x = destX;
        item.y = destY;
        changes.push({ action: "move", kind, x: destX, y: destY, at: kind });
      } else if (item) {
        changes.push({ action: "move", kind, x: item.x, y: item.y, at: kind });
      }
    }
  }

  if (tweak.deskItem && typeof tweak.deskItem === "object") {
    const owner = String(tweak.deskItem.owner || "");
    const itemName = String(tweak.deskItem.item || "");
    const desk = next.desks.find((entry) => entry.owner === owner);
    if (desk && KNOWN_ITEMS.includes(itemName) && !desk.items.includes(itemName)) {
      desk.items.push(itemName);
      changes.push({ action: "desk", kind: itemName, owner, at: "desk" });
    }
  }

  if (tweak.add && typeof tweak.add === "object") {
    const kind = String(tweak.add.kind || "");
    const x = Number(tweak.add.x);
    const y = Number(tweak.add.y);
    if (kind && KNOWN_DECOR.includes(kind) && Number.isFinite(x) && Number.isFinite(y)) {
      const exists = next.decor.some((entry) => entry.kind === kind && entry.x === x && entry.y === y);
      if (!exists) {
        const extra = {};
        if (tweak.add.text) extra.text = String(tweak.add.text).slice(0, 96);
        next.decor.push({ kind, x, y, ...extra });
        changes.push({ action: "add", kind, x, y, at: kind, added: 1 });
      }
    }
  }

  return { office: next, changes };
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
