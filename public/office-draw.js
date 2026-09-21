/** Painted diorama. Upright billboards, solid floor, one shadow. No WebGL. Fixed camera. */

export const ROOM_VOID = "#16110d";

const SKIN = {
  fair: "#f3d2b3",
  warm_light: "#e4b48a",
  warm_medium: "#c48a5a",
  deep: "#8d5a3c",
  cool_olive: "#9c8a5a",
};
const HAIR_COLOR = {
  ponytail_dark: "#3f2a1d",
  cropped_silver: "#c5c7ce",
  shoulder_brown: "#6b3f24",
  short_black_wavy: "#1b1b1b",
  bun: "#3f2a1d",
  buzz: "#2a2218",
  long_wave: "#4a2c1a",
  fade: "#1f1a16",
};
const TOP_COLOR = {
  hoodie: "#F97316",
  tee: "#14B8A6",
  blazer: "#F59E0B",
  cardigan: "#6366F1",
  flannel: "#7c2d12",
  turtleneck: "#1f2937",
};
const CAST_TOP = {
  nova: "#F97316",
  kessler: "#14B8A6",
  mira: "#F59E0B",
  jules: "#6366F1",
};
const TOP_DEEP = {
  "#F97316": "#c2410c",
  "#14B8A6": "#0f766e",
  "#F59E0B": "#b45309",
  "#6366F1": "#4338ca",
};
const BOTTOM_DEEP = {
  jeans: "#243a58",
  chinos: "#6a4e28",
  skirt: "#3f2754",
  trousers: "#1a1410",
  shorts: "#2c384e",
};
const BOTTOM_COLOR = {
  jeans: "#314e73",
  chinos: "#8a6a3d",
  skirt: "#5b3a78",
  trousers: "#2c241c",
  shorts: "#3f4f6b",
};
const SHOE_COLOR = {
  sneakers: "#efe6d4",
  boots: "#3b2418",
  loafers: "#5a3b22",
  sandals: "#c4a574",
};

const FACE = {
  ponytail_dark: "oval-pony",
  cropped_silver: "square-crop",
  shoulder_brown: "long-part",
  short_black_wavy: "round-wave",
};

/** Couch-readable stack. Hair sits on the body; clothes cover it; shoes and accessory last. */
export const HUMAN_LAYERS = ["body", "hair", "bottom", "top", "shoes", "accessory"];

export const POSE_LIBRARY = [
  "sit_type",
  "sit_think_hand",
  "stand_point",
  "walk_1",
  "walk_2",
  "stand_coffee",
  "stand_whiteboard",
];

/** Grid is not the floor hero. Seams, if any, stay under this. */
export const FLOOR_GRID_ALPHA = 0;

/** Each lamp pool stays at or under a quarter. */
export const LAMP_POOL_ALPHA = 0.22;

export function lampColor(dim = false) {
  const alpha = Math.min(0.25, dim ? LAMP_POOL_ALPHA * 0.45 : LAMP_POOL_ALPHA);
  return `rgba(255,224,138,${alpha.toFixed(2)})`;
}

export function floorMaterial(roomName = "") {
  if (/meeting|lab/i.test(roomName || "")) return "concrete";
  return "wood";
}

export function deskPlate(cell, owner = "") {
  const u = cell;
  return {
    top: u * 0.84,
    side: u * 0.48,
    wide: owner === "jules" ? u * 2.64 : u * 2.34,
  };
}

/** Marker type that still reads from the couch. */
export function whiteboardPx(cell) {
  return Math.max(36, Math.round(cell * 1.12));
}

/** ¾ upright figure. Taller than it is wide — not a top-down pancake. */
export function billboardMetrics(cell) {
  const scale = cell * 0.1;
  const height = 64 * scale;
  const width = 32 * scale;
  return { scale, height, width, upright: height > width * 1.4 };
}

/** Painter's order: smaller y (north) first, then x. South draws in front. */
export function depthOrder(entries) {
  return [...(entries || [])].sort((a, b) => {
    const dy = (a.sprite?.y || 0) - (b.sprite?.y || 0);
    if (dy) return dy;
    return (a.sprite?.x || 0) - (b.sprite?.x || 0);
  });
}

/**
 * Map the event pose (sit / walk / stand / talk / type) onto the diorama library.
 * Walk wins over the destination. Sit wins over the place. Coffee and the board
 * pick a standing gesture.
 */
export function dioramaPose(pose, frame = 0, at = "") {
  if (pose === "walk" || pose === "walk_1" || pose === "walk_2") return frame ? "walk_2" : "walk_1";
  if (pose === "sit_type" || pose === "type" || pose === "sit-type") return "sit_type";
  if (pose === "sit_think_hand" || pose === "sit" || pose === "sit-talk") return "sit_think_hand";
  if (pose === "stand_coffee") return "stand_coffee";
  if (pose === "stand_whiteboard") return "stand_whiteboard";
  if (pose === "stand_point") return "stand_point";
  if (pose === "idle" && (at === "desk" || at === "couch" || !at)) return "sit_think_hand";
  if (at === "coffee") return "stand_coffee";
  if (at === "whiteboard") return "stand_whiteboard";
  return "stand_point";
}

export function lookOf(employee) {
  const aesthetics = employee.aesthetics || {};
  const outfit = aesthetics.outfit || {};
  const hairStyle = aesthetics.hair || "short_black_wavy";
  return {
    skin: SKIN[aesthetics.skin] || SKIN.warm_medium,
    hair: HAIR_COLOR[hairStyle] || employee.color || "#1b1b1b",
    hairStyle,
    face: FACE[hairStyle] || "round-wave",
    top: CAST_TOP[employee.id] || TOP_COLOR[outfit.top] || employee.color || "#888",
    topKind: outfit.top || "tee",
    bottom: BOTTOM_COLOR[outfit.bottom] || "#314e73",
    bottomKind: outfit.bottom || "jeans",
    shoes: SHOE_COLOR[outfit.shoes] || "#efe6d4",
    accessory: outfit.accessory || "none",
  };
}

/** Baked room identity. Sprites are not part of it — they redraw on top. */
export function staticRoomKey({ office, cell, dim, w, h, ox = 0, oy = 0, employees } = {}) {
  const parts = [w, h, cell, ox, oy, dim ? 1 : 0];
  for (const room of office?.rooms || []) parts.push(room.name, room.x, room.y, room.w, room.h);
  for (const desk of office?.desks || []) {
    parts.push(desk.owner, desk.x, desk.y, desk.facing, (desk.items || []).join(","));
  }
  for (const item of office?.decor || []) {
    parts.push(item.kind, item.x, item.y, item.w || 0, item.h || 0, item.text || "");
  }
  for (const person of employees || []) {
    parts.push(person.id, person.aesthetics?.desk_style || "", person.aesthetics?.outfit?.top || "");
  }
  return parts.join("|");
}

/** Smallest clutter mark that still reads from across the room. */
export function clutterPx(cell) {
  return Math.max(12, Math.round(cell * 0.42));
}

/** One shadow recipe. Two ellipses, no radial gradient. */
export function softShadow(ctx, x, y, rx, ry, alpha = 0.4) {
  ctx.fillStyle = `rgba(20,12,8,${(alpha * 0.28).toFixed(3)})`;
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = `rgba(20,12,8,${alpha.toFixed(3)})`;
  ctx.beginPath();
  ctx.ellipse(x, y, rx * 0.58, ry * 0.55, 0, 0, Math.PI * 2);
  ctx.fill();
}

/**
 * Limb offsets in character space.
 * Sit: thigh forward, shin down. Walk: opposite arms and legs. Stand: still.
 */
export function limbPose(pose, frame = 0) {
  const step = frame ? 1 : -1;
  const sitting = pose === "sit" || pose === "sit-type" || pose === "sit-talk";
  const typing = pose === "type" || pose === "sit-type";
  const talking = pose === "talk" || pose === "sit-talk" || pose === "stand-talk";
  if (sitting) {
    return {
      posture: "sit",
      bob: 0,
      lean: 0,
      thigh: 9,
      shin: 7,
      armL: 2,
      armR: typing ? -7 - step * 2 : talking ? -5 : 3,
    };
  }
  if (pose === "walk") {
    return {
      posture: "walk",
      bob: frame ? 2 : 0,
      lean: 1.5 * step,
      thighL: 6 * step,
      thighR: -6 * step,
      shinL: step > 0 ? 4 : 8,
      shinR: step > 0 ? 8 : 4,
      armL: -7 * step,
      armR: 7 * step,
    };
  }
  return {
    posture: "stand",
    bob: 0,
    lean: 0,
    thighL: 0,
    thighR: 1,
    shinL: 13,
    shinR: 13,
    armL: talking ? -8 : 1,
    armR: talking ? -2 : 2,
  };
}

export function roundRect(ctx, x, y, w, h, r) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
  ctx.closePath();
}

export function fillVoid(ctx, w, h) {
  const wash = ctx.createRadialGradient(w * 0.36, h * 0.32, 20, w * 0.4, h * 0.4, Math.max(w, h) * 0.75);
  wash.addColorStop(0, "#2a2118");
  wash.addColorStop(1, ROOM_VOID);
  ctx.fillStyle = wash;
  ctx.fillRect(0, 0, w, h);
}

export function windowSky(dim) {
  return dim
    ? { top: "#2a3340", mid: "#3d4a3a", bottom: "#4a3a28" }
    : { top: "#8eb4c8", mid: "#c5d8c8", bottom: "#f0d2a0" };
}

function markLayer(ctx, name) {
  if (typeof ctx.markLayer === "function") ctx.markLayer(name);
}

function paintPool(ctx, x, y, rx, ry, dim) {
  const glow = ctx.createRadialGradient(x, y, 2, x, y, Math.max(rx, ry));
  glow.addColorStop(0, lampColor(dim));
  glow.addColorStop(0.55, dim ? "rgba(255,224,138,0.06)" : "rgba(255,224,138,0.09)");
  glow.addColorStop(1, "rgba(255,224,138,0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
}

function paintFloor(ctx, rx, ry, rw, rh, material, warm) {
  const g = ctx.createLinearGradient(rx, ry, rx + rw * 0.35, ry + rh);
  if (material === "concrete") {
    g.addColorStop(0, "#808692");
    g.addColorStop(0.55, "#6a707a");
    g.addColorStop(1, "#555b64");
  } else if (warm) {
    g.addColorStop(0, "#c9a06e");
    g.addColorStop(0.5, "#a67c4c");
    g.addColorStop(1, "#7c5634");
  } else {
    g.addColorStop(0, "#c49868");
    g.addColorStop(0.42, "#a67b4a");
    g.addColorStop(1, "#6d4a2c");
  }
  ctx.fillStyle = g;
  ctx.fillRect(rx, ry, rw, rh);
  if (material === "concrete") {
    ctx.fillStyle = "rgba(255,255,255,0.05)";
    ctx.fillRect(rx, ry, rw, rh * 0.16);
    ctx.fillStyle = "rgba(20,18,16,0.07)";
    ctx.beginPath();
    ctx.ellipse(rx + rw * 0.5, ry + rh * 0.58, rw * 0.34, rh * 0.18, 0, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.fillStyle = warm ? "rgba(62,34,16,0.07)" : "rgba(42,24,12,0.07)";
  const rows = 3;
  const rowH = rh / rows;
  for (let i = 1; i < rows; i += 1) {
    ctx.fillRect(rx, ry + i * rowH, rw, Math.max(2, rowH * 0.03));
  }
  ctx.fillStyle = "rgba(255,236,210,0.06)";
  ctx.fillRect(rx, ry, rw * 0.22, rh);
}

function drawWindow(ctx, x, y, w, h, dim) {
  const sky = windowSky(dim);
  const glass = ctx.createLinearGradient(x, y, x, y + h);
  glass.addColorStop(0, sky.top);
  glass.addColorStop(0.45, sky.mid);
  glass.addColorStop(1, sky.bottom);
  ctx.fillStyle = "#3a2a1c";
  roundRect(ctx, x - 4, y - 4, w + 8, h + 10, 3);
  ctx.fill();
  ctx.fillStyle = glass;
  roundRect(ctx, x, y, w, h, 2);
  ctx.fill();
  ctx.fillStyle = dim ? "rgba(20,16,12,0.22)" : "rgba(255,255,255,0.14)";
  ctx.fillRect(x + 3, y + 3, w * 0.42, h * 0.28);
  ctx.fillStyle = "#c4b08a";
  ctx.fillRect(x + w / 2 - 1.5, y + 2, 3, h - 4);
  ctx.fillRect(x + 2, y + h / 2 - 1.5, w - 4, 3);
  ctx.fillStyle = "#6b5340";
  ctx.fillRect(x - 3, y + h - 2, w + 6, 6);
}

function drawWindowLight(ctx, ox, oy, cell, item, dim) {
  const x = ox + item.x * cell;
  const y = oy + item.y * cell;
  const w = (item.w || 2) * cell;
  const tall = (item.h || 1) > 1;
  const reach = cell * (tall ? 3.4 : 4.6);
  const wash = ctx.createLinearGradient(x, y, x, y + reach);
  wash.addColorStop(0, dim ? "rgba(186,204,214,0.06)" : "rgba(186,204,214,0.16)");
  wash.addColorStop(1, "rgba(186,204,214,0)");
  ctx.fillStyle = wash;
  ctx.beginPath();
  ctx.moveTo(x + 6, y + 8);
  ctx.lineTo(x + w - 6, y + 8);
  ctx.lineTo(x + w + cell * 0.35, y + reach);
  ctx.lineTo(x - cell * 0.2, y + reach);
  ctx.closePath();
  ctx.fill();
}

function drawPlant(ctx, x, y, scale) {
  softShadow(ctx, x, y + 16 * scale, 9 * scale, 3.2 * scale, 0.34);
  ctx.fillStyle = "#5a3018";
  ctx.beginPath();
  ctx.moveTo(x - 6 * scale, y + 16 * scale);
  ctx.lineTo(x - 4 * scale, y + 8 * scale);
  ctx.lineTo(x + 4 * scale, y + 8 * scale);
  ctx.lineTo(x + 6 * scale, y + 16 * scale);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#3a1c0e";
  ctx.fillRect(x - 5 * scale, y + 14 * scale, 10 * scale, 2 * scale);
  ctx.fillStyle = "#1f5a2c";
  ctx.beginPath();
  ctx.ellipse(x - 8 * scale, y + 1 * scale, 9 * scale, 8 * scale, -0.5, 0, Math.PI * 2);
  ctx.ellipse(x + 8 * scale, y + 1 * scale, 9 * scale, 8 * scale, 0.5, 0, Math.PI * 2);
  ctx.ellipse(x, y - 8 * scale, 8 * scale, 10 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#2f6b3c";
  ctx.beginPath();
  ctx.ellipse(x - 3 * scale, y - 3 * scale, 6 * scale, 7 * scale, 0.15, 0, Math.PI * 2);
  ctx.ellipse(x + 4 * scale, y + 2 * scale, 6 * scale, 5 * scale, -0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#4c8a55";
  ctx.beginPath();
  ctx.ellipse(x, y - 2 * scale, 4 * scale, 5 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
}

export function drawOffice(ctx, { office, employees, ox, oy, cell, dim }) {
  if (!office) return;
  const rooms = office.rooms || [];
  const bounds = rooms.reduce(
    (acc, room) => ({ w: Math.max(acc.w, room.x + room.w), h: Math.max(acc.h, room.y + room.h) }),
    { w: 22, h: 16 },
  );
  const shell = ctx.createLinearGradient(ox, oy - 30, ox, oy + bounds.h * cell);
  shell.addColorStop(0, "#5a4636");
  shell.addColorStop(0.22, "#3a2c22");
  shell.addColorStop(1, "#1a140f");
  ctx.fillStyle = "#0e0b09";
  ctx.fillRect(ox - 28, oy - 40, bounds.w * cell + 56, bounds.h * cell + 72);
  ctx.fillStyle = shell;
  ctx.fillRect(ox - 14, oy - 22, bounds.w * cell + 28, bounds.h * cell + 40);
  ctx.fillStyle = "#6b5344";
  ctx.fillRect(ox - 14, oy - 22, bounds.w * cell + 28, 7);
  ctx.fillStyle = "rgba(198,214,220,0.08)";
  ctx.fillRect(ox + cell * 0.6, oy - 8, bounds.w * cell - cell * 0.4, cell * 0.16);

  for (const room of rooms) {
    const rx = ox + room.x * cell;
    const ry = oy + room.y * cell;
    const rw = room.w * cell;
    const rh = room.h * cell;
    paintFloor(ctx, rx, ry, rw, rh, floorMaterial(room.name), /break/i.test(room.name));
    ctx.fillStyle = "rgba(18,12,8,0.55)";
    ctx.fillRect(rx, ry, rw, Math.max(7, cell * 0.16));
    ctx.fillStyle = "rgba(90,68,48,0.55)";
    ctx.fillRect(rx, ry + rh - Math.max(5, cell * 0.1), rw, Math.max(5, cell * 0.1));
    ctx.fillStyle = "rgba(243,234,215,0.42)";
    ctx.font = `600 ${Math.max(10, Math.round(cell * 0.28))}px system-ui, sans-serif`;
    ctx.fillText(room.name, rx + 10, ry + Math.max(16, cell * 0.42));
  }

  for (const item of office.decor || []) {
    if (item.kind === "window") drawWindowLight(ctx, ox, oy, cell, item, dim);
  }

  for (const lamp of office.decor || []) {
    if (lamp.kind !== "lamp") continue;
    const lx = ox + lamp.x * cell + cell * 0.4;
    const ly = oy + lamp.y * cell + cell * 0.85;
    paintPool(ctx, lx, ly, cell * 3.2, cell * 1.7, dim);
  }

  for (const item of office.decor || []) {
    if (item.kind === "rug") drawDecor(ctx, ox, oy, cell, item, dim);
  }
  for (const item of office.decor || []) {
    if (item.kind !== "rug") drawDecor(ctx, ox, oy, cell, item, dim);
  }
  for (const desk of office.desks || []) {
    drawDesk(ctx, ox, oy, cell, desk, employees, dim);
  }

  if (dim) {
    ctx.fillStyle = "rgba(10,8,6,0.34)";
    ctx.fillRect(ox - 10, oy - 18, bounds.w * cell + 20, bounds.h * cell + 32);
  }
}

function drawDeskLamp(ctx, x, y, u, dim) {
  const lx = x + u * 1.85;
  const ly = y + u * 0.16;
  paintPool(ctx, lx, ly + u * 0.22, u * 0.95, u * 0.48, dim);
  ctx.fillStyle = "#2a2118";
  ctx.fillRect(lx - u * 0.035, ly, u * 0.07, u * 0.26);
  ctx.fillStyle = "#e6c36a";
  ctx.beginPath();
  ctx.moveTo(lx - u * 0.18, ly + u * 0.02);
  ctx.lineTo(lx + u * 0.18, ly + u * 0.02);
  ctx.lineTo(lx + u * 0.1, ly + u * 0.16);
  ctx.lineTo(lx - u * 0.1, ly + u * 0.16);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "rgba(255,244,210,0.55)";
  ctx.fillRect(lx - u * 0.08, ly + u * 0.05, u * 0.06, u * 0.06);
}

function drawKeyboard(ctx, x, y, u) {
  ctx.fillStyle = "#2c2c2e";
  roundRect(ctx, x + u * 0.38, y + u * 0.52, u * 0.78, u * 0.18, 2);
  ctx.fill();
  ctx.fillStyle = "#d2d2d7";
  for (let i = 0; i < 4; i += 1) {
    ctx.fillRect(x + u * 0.44 + i * u * 0.16, y + u * 0.56, u * 0.1, u * 0.05);
  }
}

function drawDesk(ctx, ox, oy, cell, desk, employees, dim) {
  const x = ox + desk.x * cell;
  const y = oy + desk.y * cell;
  const west = desk.facing === "west";
  const u = cell;
  const owner = (employees || []).find((person) => person.id === desk.owner);
  const who = desk.owner;
  const plate = deskPlate(u, who);
  const { wide, top, side } = plate;
  softShadow(ctx, x + wide * 0.5, y + top + side + u * 0.28, wide * 0.48, u * 0.16, 0.42);

  ctx.fillStyle = "#2a1c14";
  ctx.fillRect(x + u * 0.18, y + top + side * 0.35, u * 0.14, u * 0.62);
  ctx.fillRect(x + wide - u * 0.32, y + top + side * 0.35, u * 0.14, u * 0.62);

  const sideG = ctx.createLinearGradient(x, y + top * 0.35, x, y + top + side);
  sideG.addColorStop(0, "#6e4e34");
  sideG.addColorStop(1, "#3a2818");
  ctx.fillStyle = sideG;
  roundRect(ctx, x, y + top * 0.38, wide, top * 0.55 + side, 5);
  ctx.fill();

  const topG = ctx.createLinearGradient(x, y, x + wide, y + top);
  topG.addColorStop(0, "#edd7b4");
  topG.addColorStop(0.35, "#d4b48a");
  topG.addColorStop(1, "#b48a5c");
  ctx.fillStyle = topG;
  roundRect(ctx, x, y, wide, top, 6);
  ctx.fill();
  ctx.fillStyle = who === "jules" ? "#6366F1" : "rgba(255,248,236,0.28)";
  ctx.fillRect(x + 5, y + 4, wide - 10, Math.max(2, u * 0.045));
  ctx.fillStyle = "rgba(40,22,10,0.2)";
  ctx.fillRect(x + 4, y + top - u * 0.07, wide - 8, u * 0.06);
  ctx.fillStyle = "rgba(255,236,210,0.08)";
  ctx.fillRect(x + u * 0.2, y + u * 0.16, u * 0.55, u * 0.04);

  const chairX = west ? x - u * 0.15 : x + wide * 0.28;
  const chairY = y + top * 0.72;
  ctx.fillStyle = "#241c16";
  roundRect(ctx, chairX, chairY, u * 0.92, u * 0.78, 4);
  ctx.fill();
  ctx.fillStyle = "#4a4036";
  roundRect(ctx, chairX + u * 0.08, chairY + u * 0.08, u * 0.76, u * 0.28, 3);
  ctx.fill();
  ctx.fillStyle = "#3a322b";
  roundRect(ctx, chairX + u * 0.1, chairY + u * 0.34, u * 0.72, u * 0.36, 3);
  ctx.fill();

  const items = desk.items || [];
  const style = owner?.aesthetics?.desk_style || "";
  drawDeskLamp(ctx, x, y, u, dim);
  drawKeyboard(ctx, x, y, u);

  if (items.includes("monitor") || items.includes("second_monitor")) {
    const mx = x + u * 0.16;
    const my = y + u * 0.1;
    const mw = clutterPx(u) * 1.7;
    const mh = clutterPx(u) * 1.05;
    ctx.fillStyle = "#1b1b1d";
    roundRect(ctx, mx, my, mw, mh, 3);
    ctx.fill();
    const screen = ctx.createLinearGradient(mx, my, mx + mw, my + mh);
    screen.addColorStop(0, "#e7f3ff");
    screen.addColorStop(1, "#6a96b0");
    ctx.fillStyle = screen;
    ctx.fillRect(mx + 3, my + 3, mw - 6, mh - u * 0.16);
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.fillRect(mx + 5, my + 5, mw * 0.22, 3);
    ctx.fillStyle = "#2c2c2e";
    ctx.fillRect(mx + mw * 0.4, my + mh - u * 0.08, u * 0.16, u * 0.12);
    if (items.includes("second_monitor") || who === "nova") {
      ctx.fillStyle = "#1b1b1d";
      roundRect(ctx, x + u * 1.15, y + u * 0.14, mw * 0.72, mh * 0.86, 3);
      ctx.fill();
      ctx.fillStyle = "#d5e6d4";
      ctx.fillRect(x + u * 1.2, y + u * 0.2, mw * 0.58, mh * 0.58);
    }
  }
  if (items.includes("plant") || who === "jules") drawPlant(ctx, x + wide - u * 0.28, y + u * 0.42, Math.max(u * 0.055, 2.2));
  if (items.includes("coffee_mug")) {
    const mug = who === "nova" ? "#F97316" : who === "mira" ? "#F59E0B" : who === "kessler" ? "#14B8A6" : "#6366F1";
    const mw = clutterPx(u) * 0.78;
    const mh = clutterPx(u) * 0.62;
    const mx = x + u * 0.12;
    const my = y + top * 0.62;
    ctx.fillStyle = "#3a2a20";
    roundRect(ctx, mx + 2, my + 3, mw, mh, 3);
    ctx.fill();
    ctx.fillStyle = mug;
    roundRect(ctx, mx, my, mw, mh, 3);
    ctx.fill();
    ctx.fillStyle = "rgba(255,255,255,0.3)";
    ctx.fillRect(mx + 2, my + 2, Math.max(2, mw * 0.22), mh - 4);
  }
  if (who === "kessler" || items.includes("notebook")) {
    const nw = clutterPx(u) * 1.2;
    ctx.fillStyle = "#f4f0e6";
    ctx.fillRect(x + u * 1.2, y + top * 0.5, nw, nw * 0.7);
    ctx.fillStyle = "#14B8A6";
    ctx.fillRect(x + u * 1.2, y + top * 0.5, u * 0.07, nw * 0.7);
  }
  if (who === "mira" || items.includes("sticky_notes") || /kanban|sticky/i.test(style)) {
    const s = clutterPx(u) * 0.62;
    ctx.fillStyle = "#e6d36a";
    ctx.fillRect(x + u * 1.02, y + u * 0.36, s, s * 0.85);
    ctx.fillStyle = "#F97316";
    ctx.fillRect(x + u * 1.02 + s * 0.7, y + u * 0.3, s * 0.85, s * 0.8);
    ctx.fillStyle = "#14B8A6";
    ctx.fillRect(x + u * 1.15, y + u * 0.36 + s * 0.5, s * 0.8, s * 0.7);
  }
  if (who === "nova" || /messy|cable|sticker/i.test(style)) {
    ctx.strokeStyle = "#8e8e93";
    ctx.lineWidth = Math.max(1.6, u * 0.04);
    ctx.beginPath();
    ctx.moveTo(x + u * 0.2, y + top * 0.78);
    ctx.quadraticCurveTo(x + u * 0.7, y + top * 0.98, x + u * 1.15, y + top * 0.7);
    ctx.stroke();
    ctx.fillStyle = "#F97316";
    ctx.fillRect(x + u * 0.95, y + u * 0.48, u * 0.2, u * 0.12);
  }
  if (who === "jules" || /neat|label/i.test(style)) {
    ctx.fillStyle = "#f3ead7";
    ctx.fillRect(x + wide - u * 0.85, y + top * 0.48, u * 0.42, u * 0.12);
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(x + wide - u * 0.85, y + top * 0.48, u * 0.07, u * 0.12);
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + wide - u * 0.95, y + top * 0.66, u * 0.46, u * 0.16);
  }
}

function drawWhiteboard(ctx, x, y, cell, item) {
  const w = (item.w || 6) * cell;
  const size = whiteboardPx(cell);
  const lineH = Math.round(size * 1.02);
  const h = Math.max(cell * 2.05, lineH * 2 + 26);
  softShadow(ctx, x + w * 0.5, y + h * 0.92, w * 0.42, cell * 0.18, 0.28);
  ctx.fillStyle = "#4a3828";
  roundRect(ctx, x - 8, y - 8, w + 16, h + 18, 4);
  ctx.fill();
  ctx.fillStyle = "#f4f1e8";
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = "rgba(255,255,255,0.45)";
  ctx.fillRect(x + 2, y + 2, w * 0.18, h - 8);
  ctx.fillStyle = "#c4b08a";
  ctx.fillRect(x, y + h - 12, w, 12);
  ctx.fillStyle = "#2a2118";
  ctx.fillRect(x + 14, y + h - 8, 16, 5);
  ctx.fillRect(x + 36, y + h - 8, 16, 5);
  ctx.fillStyle = "#1a1410";
  ctx.font = `800 ${size}px system-ui, sans-serif`;
  const words = String(item.text || "SHIP").split(" ");
  let line = "";
  let row = 0;
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > w - 24) {
      ctx.fillText(line, x + 12, y + size + 8 + row * lineH);
      line = word;
      row += 1;
      if (row > 1) break;
    } else {
      line = next;
    }
  }
  if (line && row < 2) ctx.fillText(line, x + 12, y + size + 8 + row * lineH);
}

function drawDecor(ctx, ox, oy, cell, item, dim) {
  const x = ox + item.x * cell;
  const y = oy + item.y * cell;
  if (item.kind === "window") {
    drawWindow(ctx, x, y, (item.w || 2) * cell, (item.h || 1) * cell * 0.85, dim);
  } else if (item.kind === "whiteboard") {
    drawWhiteboard(ctx, x, y, cell, item);
  } else if (item.kind === "coffee") {
    softShadow(ctx, x + cell * 0.62, y + cell * 1.35, cell * 0.7, cell * 0.14, 0.36);
    ctx.fillStyle = "#1c1c1e";
    roundRect(ctx, x + cell * 0.1, y + cell * 0.12, cell * 1.15, cell * 1.28, 4);
    ctx.fill();
    ctx.fillStyle = "#3a3a3c";
    roundRect(ctx, x, y, cell * 1.15, cell * 1.22, 4);
    ctx.fill();
    ctx.fillStyle = "#d8d8de";
    ctx.fillRect(x + cell * 0.16, y + cell * 0.12, cell * 0.7, cell * 0.16);
    ctx.fillStyle = "#6b3a22";
    roundRect(ctx, x + cell * 0.22, y + cell * 0.48, cell * 0.36, cell * 0.2, 2);
    ctx.fill();
    ctx.fillStyle = "#e8dfd2";
    roundRect(ctx, x + cell * 0.7, y + cell * 0.7, cell * 0.26, cell * 0.32, 2);
    ctx.fill();
  } else if (item.kind === "couch") {
    softShadow(ctx, x + cell * 1.3, y + cell * 1.28, cell * 1.4, cell * 0.16, 0.36);
    ctx.fillStyle = "#2c261f";
    roundRect(ctx, x, y + cell * 0.28, cell * 2.6, cell * 1.05, 6);
    ctx.fill();
    ctx.fillStyle = "#6a5e52";
    roundRect(ctx, x + cell * 0.16, y, cell * 2.28, cell * 0.7, 6);
    ctx.fill();
    ctx.fillStyle = "#8a7b6c";
    roundRect(ctx, x + cell * 0.28, y + cell * 0.1, cell * 0.9, cell * 0.42, 4);
    ctx.fill();
    roundRect(ctx, x + cell * 1.32, y + cell * 0.1, cell * 0.9, cell * 0.42, 4);
    ctx.fill();
    ctx.fillStyle = "#3a322b";
    ctx.fillRect(x, y + cell * 0.42, cell * 0.16, cell * 0.85);
    ctx.fillRect(x + cell * 2.44, y + cell * 0.42, cell * 0.16, cell * 0.85);
  } else if (item.kind === "plant") {
    drawPlant(ctx, x + cell * 0.45, y + cell * 0.32, cell * 0.09);
  } else if (item.kind === "lamp") {
    ctx.fillStyle = "#f0e2b0";
    ctx.beginPath();
    ctx.arc(x + cell * 0.28, y + cell * 0.2, cell * 0.16, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + cell * 0.24, y + cell * 0.34, cell * 0.08, cell * 0.36);
    ctx.fillStyle = "#2a2118";
    ctx.fillRect(x + cell * 0.16, y + cell * 0.68, cell * 0.24, cell * 0.06);
  } else if (item.kind === "rug") {
    ctx.fillStyle = "#4a3024";
    roundRect(ctx, x, y, cell * 5, cell * 3, 6);
    ctx.fill();
    ctx.fillStyle = "#6b4634";
    roundRect(ctx, x + cell * 0.18, y + cell * 0.16, cell * 4.64, cell * 2.68, 4);
    ctx.fill();
    ctx.fillStyle = "#c4a574";
    ctx.fillRect(x + cell * 0.28, y + cell * 0.28, cell * 4.44, cell * 0.06);
    ctx.fillRect(x + cell * 0.28, y + cell * 2.6, cell * 4.44, cell * 0.06);
  } else if (item.kind === "shelf") {
    ctx.fillStyle = "#5a4030";
    ctx.fillRect(x, y + cell * 0.08, cell * 2.05, cell * 0.16);
    ctx.fillStyle = "#6b5340";
    ctx.fillRect(x, y, cell * 2, cell * 0.2);
    ctx.fillStyle = "#8a6d4e";
    ctx.fillRect(x + cell * 0.12, y - cell * 0.32, cell * 0.28, cell * 0.32);
    ctx.fillRect(x + cell * 0.5, y - cell * 0.26, cell * 0.24, cell * 0.26);
    ctx.fillStyle = "#2a4d32";
    ctx.fillRect(x + cell * 0.9, y - cell * 0.3, cell * 0.32, cell * 0.3);
  } else if (item.kind === "clock") {
    ctx.fillStyle = "#3a3228";
    ctx.beginPath();
    ctx.arc(x + cell * 0.32, y + cell * 0.32, cell * 0.3, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#f4efe4";
    ctx.beginPath();
    ctx.arc(x + cell * 0.32, y + cell * 0.32, cell * 0.24, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#1d1d1f";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x + cell * 0.32, y + cell * 0.32);
    ctx.lineTo(x + cell * 0.32, y + cell * 0.16);
    ctx.moveTo(x + cell * 0.32, y + cell * 0.32);
    ctx.lineTo(x + cell * 0.46, y + cell * 0.38);
    ctx.stroke();
    ctx.lineWidth = 1;
  } else if (item.kind === "filing_cabinet") {
    ctx.fillStyle = "#4e4e52";
    ctx.fillRect(x + cell * 0.1, y + cell * 0.06, cell * 1.12, cell * 1.5);
    ctx.fillStyle = "#8a8a90";
    ctx.fillRect(x, y, cell * 1.12, cell * 1.48);
    ctx.fillStyle = "#5c5c62";
    ctx.fillRect(x + cell * 0.1, y + cell * 0.16, cell * 0.9, cell * 0.28);
    ctx.fillRect(x + cell * 0.1, y + cell * 0.54, cell * 0.9, cell * 0.28);
    ctx.fillRect(x + cell * 0.1, y + cell * 0.92, cell * 0.9, cell * 0.28);
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(x + cell * 0.82, y + cell * 0.24, cell * 0.1, cell * 0.08);
  } else if (item.kind === "table") {
    softShadow(ctx, x + cell * 1.6, y + cell * 1.85, cell * 1.5, cell * 0.16, 0.36);
    ctx.fillStyle = "#5c4332";
    roundRect(ctx, x, y + cell * 0.22, cell * 3.2, cell * 1.55, 5);
    ctx.fill();
    const top = ctx.createLinearGradient(x, y, x, y + cell * 0.4);
    top.addColorStop(0, "#e4d0b0");
    top.addColorStop(1, "#b8926a");
    ctx.fillStyle = top;
    roundRect(ctx, x, y, cell * 3.2, cell * 0.42, 5);
    ctx.fill();
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + cell * 0.22, y + cell * 1.7, cell * 0.16, cell * 0.28);
    ctx.fillRect(x + cell * 2.8, y + cell * 1.7, cell * 0.16, cell * 0.28);
  } else if (item.kind === "beanbag") {
    softShadow(ctx, x + cell * 0.55, y + cell * 0.7, cell * 0.5, cell * 0.12, 0.3);
    ctx.fillStyle = "#3a414c";
    ctx.beginPath();
    ctx.ellipse(x + cell * 0.58, y + cell * 0.5, cell * 0.55, cell * 0.36, -0.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6366F1";
    ctx.beginPath();
    ctx.ellipse(x + cell * 0.55, y + cell * 0.32, cell * 0.34, cell * 0.16, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "minifridge") {
    ctx.fillStyle = "#b7b3ab";
    roundRect(ctx, x + cell * 0.06, y + cell * 0.06, cell * 0.55, cell * 0.85, 3);
    ctx.fill();
    ctx.fillStyle = "#e8e4dc";
    roundRect(ctx, x, y, cell * 0.55, cell * 0.82, 3);
    ctx.fill();
    ctx.fillStyle = "#8e8e93";
    ctx.fillRect(x + cell * 0.4, y + cell * 0.28, cell * 0.06, cell * 0.18);
  }
  const ad = item.advertises;
  if (ad) {
    ctx.font = `500 ${Math.max(11, Math.round(cell * 0.24))}px system-ui, sans-serif`;
    ctx.fillStyle = "rgba(243,234,215,0.72)";
    ctx.fillText(ad, x, y - 4);
  }
}

function handsFor(poseId, step) {
  if (poseId === "sit_type") return { L: [-22, -18], R: [20, -16] };
  if (poseId === "sit_think_hand") return { L: [-20, -14], R: [9, -46] };
  if (poseId === "stand_point") return { L: [-20, -18], R: [38, -30] };
  if (poseId === "stand_coffee") return { L: [-24, -20], R: [18, -16] };
  if (poseId === "stand_whiteboard") return { L: [-18, -18], R: [18, -78] };
  const swing = 9 * (step || -1);
  return { L: [-20, -20 + swing], R: [20, -20 - swing] };
}

function sleeveEnd(from, to) {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const len = Math.hypot(dx, dy) || 1;
  const t = Math.max(0, (len - 6) / len);
  return [from[0] + dx * t, from[1] + dy * t];
}

function paintLimb(ctx, x0, y0, x1, y1, width, color) {
  const ang = Math.atan2(y1 - y0, x1 - x0);
  const len = Math.hypot(x1 - x0, y1 - y0);
  ctx.save();
  ctx.translate(x0, y0);
  ctx.rotate(ang);
  ctx.fillStyle = color;
  roundRect(ctx, 0, -width / 2, Math.max(1, len), width, width / 2);
  ctx.fill();
  ctx.restore();
}

function paintHead(ctx, look) {
  ctx.fillStyle = look.skin;
  if (look.face === "square-crop") {
    roundRect(ctx, -10, -64, 20, 18, 3);
    ctx.fill();
  } else if (look.face === "long-part") {
    ctx.beginPath();
    ctx.ellipse(0, -54, 8.4, 11, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (look.face === "oval-pony") {
    ctx.beginPath();
    ctx.ellipse(0, -54, 9, 10.2, 0, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.arc(0, -53, 10, 0, Math.PI * 2);
    ctx.fill();
  }
}

function paintBody(ctx, look, hands, blink, talking) {
  ctx.fillStyle = "#1c140e";
  ctx.beginPath();
  ctx.ellipse(1, -53, 12.5, 13.5, 0, 0, Math.PI * 2);
  ctx.fill();
  roundRect(ctx, -19, -46, 39, 34, 8);
  ctx.fill();
  ctx.fillStyle = look.skin;
  roundRect(ctx, -3.6, -46, 7.2, 8, 2);
  ctx.fill();
  paintHead(ctx, look);
  ctx.fillStyle = look.skin;
  ctx.beginPath();
  ctx.arc(hands.L[0], hands.L[1], 3.6, 0, Math.PI * 2);
  ctx.arc(hands.R[0], hands.R[1], 3.6, 0, Math.PI * 2);
  ctx.fill();
  if (blink) {
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(-5.4, -54);
    ctx.lineTo(-1.6, -54);
    ctx.moveTo(1.8, -54);
    ctx.lineTo(5.6, -54);
    ctx.stroke();
    ctx.lineWidth = 1;
  } else if (look.face === "square-crop") {
    ctx.fillStyle = "#1b1b1b";
    ctx.fillRect(-5.6, -56, 3.4, 2.6);
    ctx.fillRect(2, -56, 3.4, 2.6);
  } else if (look.face === "long-part") {
    ctx.fillStyle = "#3a2418";
    ctx.beginPath();
    ctx.ellipse(-3.2, -55, 1.45, 2, 0, 0, Math.PI * 2);
    ctx.ellipse(3.2, -55, 1.45, 2, 0, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.fillStyle = "#1b1b1b";
    ctx.beginPath();
    ctx.arc(-3.3, -54.5, 1.45, 0, Math.PI * 2);
    ctx.arc(3.3, -54.5, 1.45, 0, Math.PI * 2);
    ctx.fill();
  }
  if (talking) {
    ctx.fillStyle = "#6b3030";
    roundRect(ctx, -2.4, -49.5, 4.8, 2.4, 1);
    ctx.fill();
  }
}

function paintHair(ctx, look) {
  ctx.fillStyle = look.hair;
  if (look.hairStyle === "ponytail_dark" || look.face === "oval-pony") {
    ctx.beginPath();
    ctx.ellipse(0, -60, 10, 6, 0, Math.PI, 0.15);
    ctx.fill();
    ctx.fillRect(-9.5, -60, 19, 5);
    ctx.beginPath();
    ctx.moveTo(7, -58);
    ctx.quadraticCurveTo(28, -50, 24, -18);
    ctx.quadraticCurveTo(18, -10, 13, -22);
    ctx.quadraticCurveTo(16, -40, 8, -54);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#2a1a12";
    ctx.fillRect(-8, -62, 7, 3.2);
  } else if (look.hairStyle === "cropped_silver" || look.face === "square-crop") {
    ctx.fillRect(-10.6, -66, 21.2, 8);
    ctx.fillRect(-10.6, -60, 4, 7);
    ctx.fillRect(6.6, -60, 4, 7);
  } else if (look.hairStyle === "shoulder_brown" || look.hairStyle === "long_wave" || look.face === "long-part") {
    ctx.beginPath();
    ctx.ellipse(0, -60, 10, 6, 0, Math.PI, 0);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(-10, -34, 5.2, 18, -0.1, 0, Math.PI * 2);
    ctx.ellipse(10, -34, 5.2, 18, 0.1, 0, Math.PI * 2);
    ctx.fill();
  } else if (look.hairStyle === "bun") {
    ctx.beginPath();
    ctx.arc(0, -68, 4.6, 0, Math.PI * 2);
    ctx.ellipse(0, -58, 10, 5.4, 0, Math.PI, 0);
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.arc(-6, -60, 5.2, 0, Math.PI * 2);
    ctx.arc(0.4, -63, 5.6, 0, Math.PI * 2);
    ctx.arc(6.4, -59, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillRect(-11, -58, 6, 8);
    ctx.fillRect(5.2, -58, 6, 7);
  }
}

function paintBottom(ctx, look, sit, step) {
  const calf = BOTTOM_DEEP[look.bottomKind] || look.bottom;
  ctx.fillStyle = look.bottom;
  if (sit && look.bottomKind === "skirt") {
    ctx.beginPath();
    ctx.moveTo(-14, -18);
    ctx.lineTo(14, -18);
    ctx.lineTo(18, 2);
    ctx.lineTo(-18, 2);
    ctx.closePath();
    ctx.fill();
    return;
  }
  if (sit) {
    roundRect(ctx, -16, -16, 14, 8, 3);
    ctx.fill();
    roundRect(ctx, 2, -16, 14, 8, 3);
    ctx.fill();
    ctx.fillStyle = calf;
    roundRect(ctx, -16, -9, 8, 12, 2);
    ctx.fill();
    roundRect(ctx, 8, -9, 8, 12, 2);
    ctx.fill();
    return;
  }
  if (look.bottomKind === "skirt") {
    ctx.beginPath();
    ctx.moveTo(-13, -18);
    ctx.lineTo(13, -18);
    ctx.lineTo(17, 0);
    ctx.lineTo(-17, 0);
    ctx.closePath();
    ctx.fill();
    return;
  }
  const shift = (step || 0) * 5;
  ctx.fillStyle = look.bottom;
  roundRect(ctx, -14 + shift, -18, 10, 12, 3);
  ctx.fill();
  roundRect(ctx, 4 - shift, -18, 10, 12, 3);
  ctx.fill();
  ctx.fillStyle = calf;
  roundRect(ctx, -13 + shift, -8, 8, 10, 2);
  ctx.fill();
  roundRect(ctx, 5 - shift, -8, 8, 10, 2);
  ctx.fill();
}

function paintTop(ctx, look, hands) {
  const deep = TOP_DEEP[look.top] || "#3a2a20";
  ctx.fillStyle = look.top;
  ctx.beginPath();
  ctx.moveTo(-18, -44);
  ctx.lineTo(18, -44);
  ctx.lineTo(15, -16);
  ctx.lineTo(-15, -16);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.14)";
  ctx.fillRect(-17, -43, 5, 22);
  ctx.fillStyle = "rgba(0,0,0,0.12)";
  ctx.beginPath();
  ctx.moveTo(8, -44);
  ctx.lineTo(18, -44);
  ctx.lineTo(15, -16);
  ctx.lineTo(7, -16);
  ctx.closePath();
  ctx.fill();
  if (look.topKind === "hoodie") {
    ctx.fillStyle = deep;
    ctx.beginPath();
    ctx.arc(0, -46, 12, Math.PI * 0.15, Math.PI * 0.85, true);
    ctx.fill();
    ctx.fillStyle = look.top;
    roundRect(ctx, -8, -32, 16, 8, 2);
    ctx.fill();
    ctx.fillStyle = deep;
    roundRect(ctx, -8, -32, 16, 8, 2);
    ctx.fill();
  } else if (look.topKind === "blazer") {
    ctx.fillStyle = deep;
    ctx.beginPath();
    ctx.moveTo(-17, -43);
    ctx.lineTo(-2, -30);
    ctx.lineTo(-9, -16);
    ctx.lineTo(-17, -16);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(17, -43);
    ctx.lineTo(2, -30);
    ctx.lineTo(9, -16);
    ctx.lineTo(17, -16);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#fde68a";
    ctx.beginPath();
    ctx.moveTo(-3.5, -42);
    ctx.lineTo(3.5, -42);
    ctx.lineTo(2.4, -18);
    ctx.lineTo(-2.4, -18);
    ctx.closePath();
    ctx.fill();
  } else if (look.topKind === "cardigan") {
    ctx.fillStyle = deep;
    ctx.fillRect(-2, -43, 4, 26);
    ctx.fillStyle = "#f3ead7";
    roundRect(ctx, -8, -40, 16, 18, 2);
    ctx.fill();
  } else if (look.topKind === "tee") {
    ctx.fillStyle = deep;
    ctx.fillRect(-8, -44, 16, 4);
  } else if (look.topKind === "turtleneck") {
    ctx.fillStyle = look.top;
    ctx.fillRect(-7, -48, 14, 6);
  } else if (look.topKind === "flannel") {
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(-14, -36, 28, 2.4);
    ctx.fillRect(-14, -28, 28, 2.4);
  }
  const left = sleeveEnd([-16, -42], hands.L);
  const right = sleeveEnd([16, -42], hands.R);
  paintLimb(ctx, -16, -42, left[0], left[1], 8.4, look.top);
  paintLimb(ctx, 16, -42, right[0], right[1], 8.4, look.top);
}

function paintShoes(ctx, look, sit, step) {
  ctx.fillStyle = look.shoes;
  const boot = look.shoes === SHOE_COLOR.boots;
  const h = boot ? 8 : 5.2;
  if (sit) {
    roundRect(ctx, -17, 1, 10, h, 1.8);
    ctx.fill();
    roundRect(ctx, 7, 1, 10, h, 1.8);
    ctx.fill();
    return;
  }
  if (look.bottomKind === "skirt") {
    roundRect(ctx, -14, -1, 9, h, 1.8);
    ctx.fill();
    roundRect(ctx, 5, -1, 9, h, 1.8);
    ctx.fill();
    return;
  }
  const shift = (step || 0) * 5;
  roundRect(ctx, -15 + shift, -1, 10, h, 1.8);
  ctx.fill();
  roundRect(ctx, 4 - shift, -1, 10, h, 1.8);
  ctx.fill();
}

function paintAccessory(ctx, look, hands, poseId) {
  if (look.accessory === "glasses") {
    ctx.strokeStyle = "#1a1a1a";
    ctx.lineWidth = 1.4;
    ctx.strokeRect(-6.6, -57.4, 5.4, 3.8);
    ctx.strokeRect(1.2, -57.4, 5.4, 3.8);
    ctx.beginPath();
    ctx.moveTo(-1.2, -55.6);
    ctx.lineTo(1.2, -55.6);
    ctx.stroke();
    ctx.lineWidth = 1;
  } else if (look.accessory === "earbuds") {
    ctx.fillStyle = "#f4f4f5";
    ctx.beginPath();
    ctx.arc(-9, -52, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#d4d4d8";
    ctx.beginPath();
    ctx.moveTo(-9, -50);
    ctx.lineTo(-9, -44);
    ctx.stroke();
  } else if (look.accessory === "watch") {
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(hands.L[0] - 3.2, hands.L[1] - 1.2, 6.4, 2.4);
  } else if (look.accessory === "keys") {
    ctx.fillStyle = "#d4af37";
    ctx.beginPath();
    ctx.arc(11, -24, 2.4, 0, Math.PI * 2);
    ctx.arc(14, -21, 1.7, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillRect(12.2, -22, 1.2, 6);
  } else if (look.accessory === "badge") {
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(4, -42, 5, 6);
  } else if (look.accessory === "scarf") {
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(-8, -49, 16, 3.2);
  }
  if (poseId === "stand_coffee") {
    ctx.fillStyle = "#e8dfd2";
    roundRect(ctx, hands.L[0] - 3.5, hands.L[1] - 8, 7, 8, 1.5);
    ctx.fill();
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(hands.L[0] - 2.2, hands.L[1] - 7, 4.4, 2);
  } else if (poseId === "stand_whiteboard") {
    ctx.fillStyle = "#1a1410";
    ctx.fillRect(hands.R[0] - 1, hands.R[1] - 8, 2.2, 8);
    ctx.fillStyle = "#e11d48";
    ctx.fillRect(hands.R[0] - 1.4, hands.R[1] - 9, 3, 2.2);
  }
}

function paintHuman(ctx, look, poseId, blink, talking) {
  const sit = poseId === "sit_type" || poseId === "sit_think_hand";
  const step = poseId === "walk_2" ? 1 : poseId === "walk_1" ? -1 : 0;
  ctx.save();
  if (sit) ctx.translate(poseId === "sit_type" ? 2 : 0, poseId === "sit_type" ? 26 : 22);
  const hands = handsFor(poseId, step);
  markLayer(ctx, "body");
  paintBody(ctx, look, hands, blink, talking);
  markLayer(ctx, "hair");
  paintHair(ctx, look);
  markLayer(ctx, "bottom");
  paintBottom(ctx, look, sit, step);
  markLayer(ctx, "top");
  paintTop(ctx, look, hands);
  markLayer(ctx, "shoes");
  paintShoes(ctx, look, sit, step);
  markLayer(ctx, "accessory");
  paintAccessory(ctx, look, hands, poseId);
  ctx.restore();
}

export function drawPerson(ctx, { employee, sprite, ox, oy, cell, now, hover, feel, verb }) {
  const poseId = sprite.poseId || dioramaPose(sprite.pose, sprite.frame || 0, sprite.at);
  const limb = limbPose(sprite.pose, sprite.frame || 0);
  const metrics = billboardMetrics(cell);
  const px = ox + sprite.x * cell + cell * 0.42;
  const footY = oy + sprite.y * cell + cell * 0.72;
  const bob = (limb.bob || 0) * metrics.scale;
  const color = employee.accent || employee.color || "#f3ead7";
  const look = lookOf(employee);
  const talking = /talk/.test(sprite.pose || "");
  const blink = now < (sprite.blinkUntil || 0);
  const acting = sprite.pose === "walk" || talking || sprite.pose === "type" || sprite.pose === "sit-type";

  softShadow(ctx, px, footY + cell * 0.04, metrics.width * 0.55, cell * 0.11, 0.4);

  ctx.save();
  ctx.translate(px, footY - bob);
  ctx.scale((sprite.facing || 1) * metrics.scale, metrics.scale);
  paintHuman(ctx, look, poseId, blink, talking);
  ctx.restore();

  if (acting) {
    ctx.save();
    ctx.globalAlpha = 0.35;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.ellipse(px, footY - metrics.height * 0.5, metrics.width * 0.72, metrics.height * 0.5, 0, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  const first = (employee.name || "").split(" ")[0];
  const seated = poseId === "sit_type" || poseId === "sit_think_hand";
  const drop = seated ? metrics.scale * (poseId === "sit_type" ? 26 : 22) : 0;
  ctx.font = `700 ${Math.max(18, Math.round(cell * 0.5))}px system-ui, sans-serif`;
  const tw = ctx.measureText(first).width;
  const plateW = tw + 20;
  const plateH = Math.max(22, cell * 0.46);
  const plateY = footY + drop + cell * 0.04;
  ctx.fillStyle = "rgba(22,16,12,0.92)";
  roundRect(ctx, px - plateW / 2, plateY, plateW, plateH, 3);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillRect(px - plateW / 2, plateY, 5, plateH);
  ctx.fillStyle = "#f7f1e6";
  ctx.fillText(first, px - tw / 2 + 2, plateY + plateH * 0.72);

  if (verb && hover) {
    ctx.font = `${Math.max(11, Math.round(cell * 0.26))}px system-ui, sans-serif`;
    const vw = ctx.measureText(verb).width;
    const verbY = footY - metrics.height - cell * 0.08;
    ctx.fillStyle = "rgba(26,20,16,0.8)";
    roundRect(ctx, px - vw / 2 - 6, verbY, vw + 12, cell * 0.32, 2);
    ctx.fill();
    ctx.fillStyle = "#c4b8a8";
    ctx.fillText(verb, px - vw / 2, verbY + cell * 0.22);
  }

  if (hover) {
    const chip = `${first} · ${employee.model || employee.modelFamily || ""}`;
    ctx.font = `${Math.max(11, Math.round(cell * 0.28))}px system-ui, sans-serif`;
    const cw = ctx.measureText(chip).width;
    ctx.fillStyle = "#1a1410";
    roundRect(ctx, px - cw / 2 - 8, footY - metrics.height - cell * 0.42, cw + 16, cell * 0.36, 3);
    ctx.fill();
    ctx.fillStyle = "#f3ead7";
    ctx.fillText(chip, px - cw / 2, footY - metrics.height - cell * 0.16);
    if (feel) {
      ctx.fillStyle = "#c4b8a8";
      ctx.fillText(feel, px - cw / 2, footY - metrics.height - cell * 0.5);
    }
  }
}

export function drawBubble(ctx, { bubble, sprite, ox, oy, cell, now, enter, hold, fade }) {
  if (!bubble || !sprite) return false;
  const age = now - bubble.born;
  const life = enter + hold + fade;
  if (age > life) return false;
  let alpha = 1;
  let scale = 1;
  if (age < enter) {
    scale = 0.86 + 0.14 * (age / enter);
    alpha = age / enter;
  } else if (age > enter + hold) {
    alpha = 1 - (age - enter - hold) / fade;
  }
  const x = ox + sprite.x * cell + cell * 0.2;
  const y = oy + sprite.y * cell - cell * 2.15;
  ctx.save();
  ctx.globalAlpha = Math.max(0, alpha);
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  const lines = (bubble.lines || []).slice(0, 2);
  const fontPx = Math.max(15, Math.round((cell || 30) * 0.38));
  ctx.font = `600 ${fontPx}px system-ui, sans-serif`;
  const w = Math.max(24, ...lines.map((line) => ctx.measureText(line).width)) + 20;
  const h = 12 + lines.length * (fontPx + 4);
  ctx.fillStyle = "#f7f1e6";
  roundRect(ctx, 0, 0, w, h, 7);
  ctx.fill();
  ctx.strokeStyle = bubble.color || "#c4b8a8";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(12, h);
  ctx.lineTo(6, h + 9);
  ctx.lineTo(22, h);
  ctx.fill();
  ctx.fillStyle = "#1a1410";
  lines.forEach((line, i) => ctx.fillText(line, 10, fontPx + 4 + i * (fontPx + 4)));
  ctx.restore();
  return true;
}
