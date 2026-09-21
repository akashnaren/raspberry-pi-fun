/** Event-log destinations and a locked room frame. No camera chase. */

export function officeBounds(office) {
  const rooms = office?.rooms || [];
  if (!rooms.length) return { minX: 0, minY: 0, maxX: 22, maxY: 16, w: 22, h: 16 };
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const room of rooms) {
    minX = Math.min(minX, room.x);
    minY = Math.min(minY, room.y);
    maxX = Math.max(maxX, room.x + room.w);
    maxY = Math.max(maxY, room.y + room.h);
  }
  return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY };
}

/** Fit the whole office in the pane. Origin is static. Never pans. */
export function fitView(office, viewW, viewH, pad = 8) {
  const bounds = officeBounds(office);
  const width = Math.max(1, viewW);
  const height = Math.max(1, viewH);
  const cell = Math.max(
    14,
    Math.min(56, Math.floor(Math.min((width - pad * 2) / bounds.w, (height - pad * 2) / bounds.h))),
  );
  const ox = Math.round((width - bounds.w * cell) / 2 - bounds.minX * cell);
  const oy = Math.round((height - bounds.h * cell) / 2 - bounds.minY * cell);
  return { ox, oy, cell, scale: cell / 30, locked: true };
}

export function standAtDesk(office, id) {
  const desk = (office?.desks || []).find((item) => item.owner === id);
  if (!desk) return { x: 4, y: 6, at: "desk" };
  return { x: desk.x + 1, y: desk.y + 2, at: "desk" };
}

export function findDecor(office, kind) {
  return (office?.decor || []).find((item) => item.kind === kind) || null;
}

export function findRoom(office, name) {
  const re = new RegExp(name, "i");
  return (office?.rooms || []).find((room) => re.test(room.name)) || null;
}

function atDecor(item, at, extraX = 1, extraY = 1) {
  if (!item) return null;
  return { x: item.x + extraX, y: item.y + extraY, at };
}

function atRoom(room, at) {
  if (!room) return null;
  return { x: room.x + Math.floor(room.w / 2), y: room.y + Math.floor(room.h / 2), at };
}

function blob(event) {
  const data = event?.data || {};
  return [event?.message, data.text, data.task?.text, data.path, data.item, data.reason, data.kind]
    .filter(Boolean)
    .join(" ");
}

const CAST_NAMES = [
  ["nova", /\bnova\b/i],
  ["kessler", /\bkessler\b/i],
  ["mira", /\bmira\b/i],
  ["jules", /\bjules\b/i],
];

/** Named person in a say() — they walk over. Event-driven chat, not a screensaver. */
export function companionId(event) {
  if (!event || event.actor === "system") return null;
  const to = String(event.data?.to || "").toLowerCase();
  if (CAST_NAMES.some(([id]) => id === to) && to !== event.actor) return to;
  if (event.type !== "say") return null;
  const text = blob(event);
  for (const [id, re] of CAST_NAMES) {
    if (id !== event.actor && re.test(text)) return id;
  }
  return null;
}

export function standBeside(target, dx = 1, dy = 0) {
  if (!target) return null;
  return { x: target.x + dx, y: target.y + dy, at: target.at };
}

/**
 * Walk to a real object because an event happened.
 * Idle is always at a thing. Never "thinking".
 */
export function destinationFor(event, office) {
  if (!office || !event || event.actor === "system") return null;
  const text = blob(event);
  const desk = () => standAtDesk(office, event.actor);
  const board = () => atDecor(findDecor(office, "whiteboard"), "whiteboard", 2, 2);
  const coffee = () => atDecor(findDecor(office, "coffee"), "coffee", 1, 1);
  const couch = () => atDecor(findDecor(office, "couch"), "couch", 1, 1);
  const meeting = () =>
    atDecor(findDecor(office, "table"), "meeting", 1, 1) ||
    atRoom(findRoom(office, "meeting|lab"), "meeting");
  const jules = () => standAtDesk(office, "jules");

  switch (event.type) {
    case "task_added":
    case "task_closed":
      return board() || desk();
    case "build_failed":
      return meeting() || board() || desk();
    case "build_passed":
      return board() || coffee() || desk();
    case "request_filed":
    case "request_decided":
      return jules();
    case "office_edited": {
      if (event.data?.at === "whiteboard" || event.data?.kind === "whiteboard") return board() || desk();
      if (Number.isFinite(Number(event.data?.x)) && Number.isFinite(Number(event.data?.y))) {
        return {
          x: Number(event.data.x),
          y: Number(event.data.y),
          at: event.data.at || event.data.kind || "coffee",
        };
      }
      return coffee() || board() || desk();
    }
    case "aesthetics_changed":
    case "journal":
      return desk();
    case "file_written":
    case "file_read": {
      const path = String(event.data?.path || "");
      if (/backlog|strategy|board\//i.test(path)) return board() || desk();
      if (/office\.json|requests\.json/i.test(path)) return coffee() || desk();
      return desk();
    }
    case "say": {
      if (/coffee|mug|caffeine|espresso/i.test(text)) return coffee() || desk();
      if (/break|couch|hang out|sit/i.test(text)) return couch() || coffee() || desk();
      if (/review|meeting|table|fail|reject|green build|bug/i.test(text)) return meeting() || desk();
      if (/board|plan|ship|task|backlog|docs|sheets|slides|grid|print|heading|find|paste-csv|paste →/i.test(text)) {
        return board() || desk();
      }
      return coffee() || couch() || desk();
    }
    case "turn_started": {
      const role = event.data?.role;
      if (role === "qa") return meeting() || desk();
      if (role === "producer") return board() || desk();
      if (role === "office_manager") return coffee() || desk();
      return desk();
    }
    default:
      return desk();
  }
}

export function poseFor(event) {
  if (!event) return "idle";
  if (event.type === "file_written" || event.type === "file_read" || event.type === "journal") {
    return "type";
  }
  if (event.type === "say") return "talk";
  if (event.type === "task_added" || event.type === "task_closed") return "talk";
  return "idle";
}

const PLACE = {
  desk: "their desk",
  whiteboard: "the whiteboard",
  coffee: "the coffee machine",
  couch: "the couch",
  meeting: "the meeting table",
  plant: "the plant",
  beanbag: "the beanbag",
  lamp: "the lamp",
};

export function placeName(at) {
  return PLACE[at] || PLACE.desk;
}

/** Sims rule: always at an object. Never abstractly thinking. */
export function verbFor(pose, at) {
  const place = placeName(at);
  if (pose === "walk") return `walking to ${place}`;
  if (pose === "type") return `at ${place}, typing`;
  if (pose === "talk") return `at ${place}`;
  return `at ${place}`;
}

export function latestEventLine(events) {
  const skip = new Set(["model_resolved", "turn_finished"]);
  const lines = (events || [])
    .filter((item) => item && !skip.has(item.type))
    .map((item) => item.headline || item.message)
    .filter((line) => line && !/^\s*\{/.test(line));
  return lines.at(-1) || "the room is still";
}

export function hudStatus({ dayN = 1, staff = 4, spentUsd = 0, ceilingUsd = 5, actingName, sleeping, paused, replay }) {
  const day = `Day ${dayN}`;
  const people = `${staff} ${staff === 1 ? "person" : "people"}`;
  const burn = `$${Number(spentUsd).toFixed(2)} / $${Number(ceilingUsd).toFixed(0)}`;
  let who = "waiting";
  if (sleeping) who = "asleep";
  else if (replay) who = "replay";
  else if (paused) who = "paused";
  else if (actingName) who = actingName.split(" ")[0];
  return { day, people, burn, who, line: `${day} · ${people} · ${burn} · ${who}` };
}
