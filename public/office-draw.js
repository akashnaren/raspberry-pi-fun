/** 2D dollhouse. No WebGL. Fixed camera. Readable across a room on HDMI. */

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
  tee: "#e8e0d2",
  blazer: "#F59E0B",
  cardigan: "#6366F1",
  flannel: "#7c2d12",
  turtleneck: "#1f2937",
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

export function lookOf(employee) {
  const aesthetics = employee.aesthetics || {};
  const outfit = aesthetics.outfit || {};
  const hairStyle = aesthetics.hair || "short_black_wavy";
  return {
    skin: SKIN[aesthetics.skin] || SKIN.warm_medium,
    hair: HAIR_COLOR[hairStyle] || employee.color || "#1b1b1b",
    hairStyle,
    face: FACE[hairStyle] || "round-wave",
    top: TOP_COLOR[outfit.top] || employee.color || "#888",
    topKind: outfit.top || "tee",
    bottom: BOTTOM_COLOR[outfit.bottom] || "#314e73",
    bottomKind: outfit.bottom || "jeans",
    shoes: SHOE_COLOR[outfit.shoes] || "#efe6d4",
    accessory: outfit.accessory || "none",
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
  ctx.fillStyle = "rgba(255,196,110,0.04)";
  for (let i = 0; i < 18; i += 1) {
    ctx.beginPath();
    ctx.arc(40 + (i * 97) % w, 30 + (i * 53) % h, 1.2, 0, Math.PI * 2);
    ctx.fill();
  }
}

function floorTone(room) {
  if (/break/i.test(room.name)) return { a: "#7a5a40", b: "#624832", grout: "#3a2c22" };
  if (/meeting|lab/i.test(room.name)) return { a: "#5c6168", b: "#4a4f56", grout: "#2c3036" };
  return { a: "#8a6a48", b: "#73563a", grout: "#3d2e22" };
}

function drawPlanks(ctx, rx, ry, rw, rh, cell, tone) {
  ctx.fillStyle = tone.a;
  ctx.fillRect(rx, ry, rw, rh);
  const rows = Math.max(1, Math.round(rh / (cell * 0.45)));
  const rowH = rh / rows;
  for (let row = 0; row < rows; row += 1) {
    const y = ry + row * rowH;
    ctx.fillStyle = row % 2 === 0 ? tone.a : tone.b;
    ctx.fillRect(rx, y, rw, rowH);
    ctx.strokeStyle = "rgba(20,12,8,0.28)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(rx, y + 0.5);
    ctx.lineTo(rx + rw, y + 0.5);
    ctx.stroke();
    const stagger = row % 2 === 0 ? 0 : cell * 0.6;
    for (let x = rx + stagger; x < rx + rw; x += cell * 1.7) {
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x, y + rowH);
      ctx.stroke();
    }
  }
}

function drawTiles(ctx, rx, ry, rw, rh, cell, tone) {
  ctx.fillStyle = tone.grout;
  ctx.fillRect(rx, ry, rw, rh);
  const tile = Math.max(10, cell * 0.7);
  for (let y = ry; y < ry + rh; y += tile) {
    for (let x = rx; x < rx + rw; x += tile) {
      const odd = Math.floor((x + y) / tile) % 2;
      ctx.fillStyle = odd ? tone.a : tone.b;
      ctx.fillRect(x + 1, y + 1, tile - 2, tile - 2);
    }
  }
}

export function windowSky(dim) {
  return dim
    ? { top: "#2a3340", mid: "#3d4a3a", bottom: "#4a3a28" }
    : { top: "#8eb4c8", mid: "#c5d8c8", bottom: "#f0d2a0" };
}

function drawWindow(ctx, x, y, w, h, dim) {
  const sky = windowSky(dim);
  const glass = ctx.createLinearGradient(x, y, x, y + h);
  glass.addColorStop(0, sky.top);
  glass.addColorStop(0.45, sky.mid);
  glass.addColorStop(1, sky.bottom);
  ctx.fillStyle = "#3a2a1c";
  roundRect(ctx, x - 3, y - 3, w + 6, h + 8, 3);
  ctx.fill();
  ctx.fillStyle = glass;
  roundRect(ctx, x, y, w, h, 2);
  ctx.fill();
  ctx.fillStyle = dim ? "rgba(20,16,12,0.28)" : "rgba(255,236,200,0.16)";
  ctx.fillRect(x + 3, y + 3, w / 2 - 5, h * 0.42);
  ctx.strokeStyle = "#c4b08a";
  ctx.lineWidth = 2;
  ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
  ctx.beginPath();
  ctx.moveTo(x + w / 2, y + 2);
  ctx.lineTo(x + w / 2, y + h - 2);
  ctx.moveTo(x + 2, y + h / 2);
  ctx.lineTo(x + w - 2, y + h / 2);
  ctx.stroke();
  ctx.fillStyle = "#6b5340";
  ctx.fillRect(x - 2, y + h - 2, w + 4, 5);
  ctx.lineWidth = 1;
}

function drawWindowLight(ctx, ox, oy, cell, item, dim) {
  const x = ox + item.x * cell;
  const y = oy + item.y * cell;
  const w = (item.w || 2) * cell;
  const tall = (item.h || 1) > 1;
  const reach = cell * (tall ? 3.8 : 5.4);
  const wash = ctx.createLinearGradient(x, y, x, y + reach);
  wash.addColorStop(0, dim ? "rgba(255,196,110,0.14)" : "rgba(255,224,138,0.42)");
  wash.addColorStop(0.45, dim ? "rgba(255,196,110,0.06)" : "rgba(245,185,66,0.16)");
  wash.addColorStop(1, "rgba(255,196,110,0)");
  ctx.fillStyle = wash;
  ctx.beginPath();
  ctx.moveTo(x + 4, y + 6);
  ctx.lineTo(x + w - 4, y + 6);
  ctx.lineTo(x + w + cell * 0.7, y + reach);
  ctx.lineTo(x - cell * 0.45, y + reach);
  ctx.closePath();
  ctx.fill();
  const pool = ctx.createRadialGradient(x + w * 0.5, y + reach * 0.72, 4, x + w * 0.5, y + reach * 0.72, cell * 1.8);
  pool.addColorStop(0, dim ? "rgba(255,224,138,0.08)" : "rgba(255,224,138,0.2)");
  pool.addColorStop(1, "rgba(255,224,138,0)");
  ctx.fillStyle = pool;
  ctx.beginPath();
  ctx.ellipse(x + w * 0.5, y + reach * 0.72, cell * 1.7, cell * 0.42, 0, 0, Math.PI * 2);
  ctx.fill();
}

function drawFloorShadow(ctx, x, y, rx, ry) {
  ctx.fillStyle = "rgba(0,0,0,0.36)";
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
}

function drawPlant(ctx, x, y, scale, sway = 0) {
  ctx.fillStyle = "rgba(0,0,0,0.28)";
  ctx.beginPath();
  ctx.ellipse(x, y + 16 * scale, 8 * scale, 3 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
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
  ctx.ellipse(x - 8 * scale + sway, y + 1 * scale, 9 * scale, 8 * scale, -0.5, 0, Math.PI * 2);
  ctx.ellipse(x + 8 * scale + sway, y + 1 * scale, 9 * scale, 8 * scale, 0.5, 0, Math.PI * 2);
  ctx.ellipse(x + sway, y - 8 * scale, 8 * scale, 10 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#2f6b3c";
  ctx.beginPath();
  ctx.ellipse(x - 3 * scale + sway, y - 3 * scale, 6 * scale, 7 * scale, 0.15, 0, Math.PI * 2);
  ctx.ellipse(x + 4 * scale + sway, y + 2 * scale, 6 * scale, 5 * scale, -0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#4c8a55";
  ctx.beginPath();
  ctx.ellipse(x + sway, y - 2 * scale, 4 * scale, 5 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
}

export function drawOffice(ctx, { office, employees, ox, oy, cell, dim }) {
  if (!office) return;
  const rooms = office.rooms || [];
  const bounds = rooms.reduce(
    (acc, room) => ({ w: Math.max(acc.w, room.x + room.w), h: Math.max(acc.h, room.y + room.h) }),
    { w: 22, h: 16 },
  );
  const wall = office.walls || "#2a2118";
  ctx.fillStyle = "#0e0b09";
  ctx.fillRect(ox - 28, oy - 36, bounds.w * cell + 56, bounds.h * cell + 64);
  ctx.fillStyle = "#3a2a1c";
  ctx.fillRect(ox - 16, oy - 28, bounds.w * cell + 32, 18);
  ctx.fillStyle = wall;
  ctx.fillRect(ox - 12, oy - 14, bounds.w * cell + 24, bounds.h * cell + 32);
  ctx.fillStyle = "#4a3424";
  ctx.fillRect(ox - 12, oy - 14, bounds.w * cell + 24, 8);

  for (const room of rooms) {
    const rx = ox + room.x * cell;
    const ry = oy + room.y * cell;
    const rw = room.w * cell;
    const rh = room.h * cell;
    const tone = floorTone(room);
    if (/meeting|lab/i.test(room.name)) drawTiles(ctx, rx, ry, rw, rh, cell, tone);
    else drawPlanks(ctx, rx, ry, rw, rh, cell, tone);

    ctx.fillStyle = "#1a1410";
    ctx.fillRect(rx, ry, rw, 7);
    ctx.fillRect(rx, ry + rh - 4, rw, 4);
    ctx.fillRect(rx, ry, 4, rh);
    ctx.fillRect(rx + rw - 4, ry, 4, rh);
    ctx.fillStyle = "#3a2a1c";
    ctx.fillRect(rx + 4, ry + 7, rw - 8, 3);

    ctx.fillStyle = "#c4b8a8";
    ctx.font = `600 ${Math.max(9, Math.round(cell * 0.32))}px system-ui, sans-serif`;
    ctx.fillText(room.name, rx + 10, ry + 20);
  }

  for (const item of office.decor || []) {
    if (item.kind === "window") drawWindowLight(ctx, ox, oy, cell, item, dim);
  }

  for (const lamp of office.decor || []) {
    if (lamp.kind !== "lamp") continue;
    const lx = ox + lamp.x * cell + cell * 0.35;
    const ly = oy + lamp.y * cell + cell * 0.25;
    const glow = ctx.createRadialGradient(lx, ly, 2, lx, ly, cell * 2.9);
    glow.addColorStop(0, dim ? "rgba(255,224,138,0.2)" : "rgba(255,224,138,0.48)");
    glow.addColorStop(0.4, dim ? "rgba(245,185,66,0.1)" : "rgba(245,185,66,0.2)");
    glow.addColorStop(1, "rgba(255,196,110,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.ellipse(lx, ly + cell * 0.15, cell * 2.6, cell * 1.7, 0, 0, Math.PI * 2);
    ctx.fill();
  }

  for (const item of office.decor || []) {
    if (item.kind === "rug") drawDecor(ctx, ox, oy, cell, item, dim);
  }
  for (const item of office.decor || []) {
    if (item.kind !== "rug") drawDecor(ctx, ox, oy, cell, item, dim);
  }
  for (const desk of office.desks || []) {
    drawDesk(ctx, ox, oy, cell, desk, employees);
  }

  if (dim) {
    ctx.fillStyle = "rgba(10,8,6,0.34)";
    ctx.fillRect(ox - 10, oy - 14, bounds.w * cell + 20, bounds.h * cell + 28);
  }
}

function drawDeskLamp(ctx, x, y, u) {
  const lx = x + u * 1.95;
  const ly = y + u * 0.18;
  const glow = ctx.createRadialGradient(lx, ly + u * 0.12, 2, lx, ly + u * 0.12, u * 0.85);
  glow.addColorStop(0, "rgba(255,224,138,0.32)");
  glow.addColorStop(1, "rgba(255,196,110,0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.ellipse(lx, ly + u * 0.2, u * 0.72, u * 0.38, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#2a2118";
  ctx.fillRect(lx - u * 0.04, ly, u * 0.08, u * 0.28);
  ctx.fillStyle = "#F5B942";
  ctx.beginPath();
  ctx.moveTo(lx - u * 0.16, ly + u * 0.06);
  ctx.lineTo(lx + u * 0.16, ly + u * 0.06);
  ctx.lineTo(lx + u * 0.1, ly + u * 0.2);
  ctx.lineTo(lx - u * 0.1, ly + u * 0.2);
  ctx.closePath();
  ctx.fill();
}

function drawKeyboard(ctx, x, y, u) {
  ctx.fillStyle = "#2c2c2e";
  roundRect(ctx, x + u * 0.42, y + u * 0.78, u * 0.72, u * 0.22, 2);
  ctx.fill();
  ctx.fillStyle = "#d2d2d7";
  for (let i = 0; i < 4; i += 1) {
    ctx.fillRect(x + u * 0.48 + i * u * 0.16, y + u * 0.82, u * 0.1, u * 0.05);
  }
}

function drawDesk(ctx, ox, oy, cell, desk, employees) {
  const x = ox + desk.x * cell;
  const y = oy + desk.y * cell;
  const west = desk.facing === "west";
  const u = cell;
  drawFloorShadow(ctx, x + u * 1.12, y + u * 1.7, u * 1.38, u * 0.24);

  ctx.fillStyle = "#3a2a20";
  ctx.fillRect(x + u * 0.12, y + u * 1.12, u * 0.14, u * 0.4);
  ctx.fillRect(x + u * 1.9, y + u * 1.12, u * 0.14, u * 0.4);

  ctx.fillStyle = "#8a6d4e";
  roundRect(ctx, x, y, u * 2.25, u * 1.28, 5);
  ctx.fill();
  ctx.fillStyle = "#c4a574";
  ctx.fillRect(x + u * 0.08, y + u * 0.08, u * 2.1, u * 0.12);
  ctx.fillStyle = "#6b5340";
  ctx.fillRect(x + u * 0.1, y + u * 1.08, u * 2.05, u * 0.08);

  const chairX = west ? x + u * 1.45 : x + u * 0.2;
  ctx.fillStyle = "#3a322b";
  roundRect(ctx, chairX, y + u * 1.32, u * 0.78, u * 0.48, 4);
  ctx.fill();
  ctx.fillStyle = "#2a2118";
  ctx.fillRect(chairX + u * 0.28, y + u * 1.2, u * 0.22, u * 0.18);

  const items = desk.items || [];
  const owner = (employees || []).find((person) => person.id === desk.owner);
  const style = owner?.aesthetics?.desk_style || "";
  const who = desk.owner;

  drawDeskLamp(ctx, x, y, u);
  drawKeyboard(ctx, x, y, u);

  if (items.includes("monitor") || items.includes("second_monitor")) {
    const mx = x + u * 0.18;
    const my = y + u * 0.14;
    ctx.fillStyle = "#1b1b1d";
    roundRect(ctx, mx, my, u * 1.02, u * 0.6, 3);
    ctx.fill();
    const screen = ctx.createLinearGradient(mx, my, mx + u * 0.95, my + u * 0.52);
    screen.addColorStop(0, "#d7ecff");
    screen.addColorStop(1, "#6a96b0");
    ctx.fillStyle = screen;
    ctx.fillRect(mx + u * 0.06, my + u * 0.08, u * 0.9, u * 0.44);
    ctx.fillStyle = "#2c2c2e";
    ctx.fillRect(mx + u * 0.44, my + u * 0.6, u * 0.16, u * 0.14);
    if (items.includes("second_monitor") || who === "nova") {
      ctx.fillStyle = "#1b1b1d";
      roundRect(ctx, x + u * 1.18, y + u * 0.2, u * 0.66, u * 0.5, 3);
      ctx.fill();
      ctx.fillStyle = "#c5d8c8";
      ctx.fillRect(x + u * 1.24, y + u * 0.26, u * 0.54, u * 0.36);
    }
  }
  if (items.includes("plant") || who === "jules") drawPlant(ctx, x + u * 2.08, y + u * 0.5, u * 0.05);
  if (items.includes("coffee_mug")) {
    const mug = who === "nova" ? "#F97316" : who === "mira" ? "#F59E0B" : who === "kessler" ? "#14B8A6" : "#e8dfd2";
    ctx.fillStyle = mug;
    roundRect(ctx, x + u * 0.16, y + u * 0.78, u * 0.22, u * 0.2, 2);
    ctx.fill();
    ctx.strokeStyle = mug;
    ctx.lineWidth = Math.max(1.5, u * 0.04);
    ctx.beginPath();
    ctx.arc(x + u * 0.4, y + u * 0.88, u * 0.07, -0.7, 0.7);
    ctx.stroke();
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + u * 0.19, y + u * 0.8, u * 0.16, u * 0.08);
  }
  if (who === "kessler" || items.includes("notebook")) {
    ctx.fillStyle = "#f2efe8";
    ctx.fillRect(x + u * 1.42, y + u * 0.78, u * 0.4, u * 0.3);
    ctx.fillStyle = "#d8cbb0";
    ctx.fillRect(x + u * 1.46, y + u * 0.82, u * 0.36, u * 0.26);
    ctx.fillStyle = "#14B8A6";
    ctx.fillRect(x + u * 1.42, y + u * 0.78, u * 0.06, u * 0.3);
  }
  if (who === "mira" || items.includes("sticky_notes") || /kanban|sticky/i.test(style)) {
    ctx.fillStyle = "#e6d36a";
    ctx.fillRect(x + u * 1.18, y + u * 0.62, u * 0.2, u * 0.18);
    ctx.fillStyle = "#F97316";
    ctx.fillRect(x + u * 1.36, y + u * 0.56, u * 0.18, u * 0.16);
    ctx.fillStyle = "#14B8A6";
    ctx.fillRect(x + u * 1.28, y + u * 0.76, u * 0.16, u * 0.14);
    ctx.fillStyle = "#F59E0B";
    ctx.fillRect(x + u * 1.48, y + u * 0.68, u * 0.14, u * 0.12);
  }
  if (who === "nova" || /messy|cable|sticker/i.test(style)) {
    ctx.strokeStyle = "#8e8e93";
    ctx.lineWidth = Math.max(1.4, u * 0.045);
    ctx.beginPath();
    ctx.moveTo(x + u * 0.18, y + u * 0.92);
    ctx.quadraticCurveTo(x + u * 0.7, y + u * 1.14, x + u * 1.12, y + u * 0.86);
    ctx.stroke();
    ctx.fillStyle = "#F97316";
    ctx.fillRect(x + u * 1.02, y + u * 0.66, u * 0.18, u * 0.14);
    ctx.fillStyle = "#1d1d1f";
    ctx.beginPath();
    ctx.arc(x + u * 0.3, y + u * 0.62, u * 0.07, 0, Math.PI * 2);
    ctx.fill();
  }
  if (who === "jules" || /neat|label/i.test(style)) {
    ctx.fillStyle = "#e8e0d2";
    ctx.fillRect(x + u * 1.62, y + u * 0.64, u * 0.32, u * 0.12);
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(x + u * 1.62, y + u * 0.64, u * 0.06, u * 0.12);
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + u * 1.5, y + u * 0.84, u * 0.42, u * 0.22);
    ctx.fillStyle = "#c4a574";
    ctx.fillRect(x + u * 1.54, y + u * 0.88, u * 0.34, u * 0.04);
  }
  const tag = (owner?.name || desk.owner).split(" ")[0];
  ctx.font = `600 ${Math.max(12, Math.round(u * 0.36))}px system-ui, sans-serif`;
  const tw = ctx.measureText(tag).width;
  const tagY = y - u * 0.46;
  ctx.fillStyle = "#1a1410";
  roundRect(ctx, x + u * 0.08, tagY, tw + u * 0.4, u * 0.38, 3);
  ctx.fill();
  ctx.fillStyle = owner?.accent || "#f3ead7";
  ctx.fillRect(x + u * 0.08, tagY, u * 0.1, u * 0.38);
  ctx.fillStyle = "#f3ead7";
  ctx.fillText(tag, x + u * 0.24, tagY + u * 0.26);
}

function drawDecor(ctx, ox, oy, cell, item, dim) {
  const x = ox + item.x * cell;
  const y = oy + item.y * cell;
  if (item.kind === "window") {
    drawWindow(ctx, x, y, (item.w || 2) * cell, (item.h || 1) * cell * 0.85, dim);
  } else if (item.kind === "whiteboard") {
    const w = (item.w || 6) * cell;
    const h = Math.max(cell * 1.85, (item.h || 1) * cell * 1.15);
    ctx.fillStyle = "#1a1410";
    ctx.fillRect(x - 6, y - 6, w + 12, h + 12);
    ctx.fillStyle = "#f7f1e4";
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "#d8cbb0";
    ctx.fillRect(x, y + h - 10, w, 10);
    ctx.fillStyle = "#2a2118";
    ctx.fillRect(x + 12, y + h - 7, 12, 5);
    ctx.fillRect(x + 28, y + h - 7, 12, 5);
    ctx.fillStyle = "#1a1410";
    const size = Math.max(22, Math.round(cell * 0.72));
    ctx.font = `800 ${size}px system-ui, sans-serif`;
    const words = String(item.text || "SHIP").split(" ");
    let line = "";
    let row = 0;
    const lineH = size + 8;
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (ctx.measureText(next).width > w - 20) {
        ctx.fillText(line, x + 10, y + size + 10 + row * lineH);
        line = word;
        row += 1;
        if (row > 2) break;
      } else {
        line = next;
      }
    }
    if (line && row < 3) ctx.fillText(line, x + 10, y + size + 10 + row * lineH);
  } else if (item.kind === "coffee") {
    drawFloorShadow(ctx, x + cell * 0.6, y + cell * 1.28, cell * 0.62, cell * 0.12);
    ctx.fillStyle = "#2c2c2e";
    roundRect(ctx, x, y, cell * 1.2, cell * 1.25, 4);
    ctx.fill();
    ctx.fillStyle = "#d2d2d7";
    ctx.fillRect(x + cell * 0.18, y + cell * 0.14, cell * 0.55, cell * 0.22);
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(x + cell * 0.22, y + cell * 0.48, cell * 0.32, cell * 0.14);
    ctx.fillStyle = "#e8dfd2";
    ctx.fillRect(x + cell * 0.72, y + cell * 0.72, cell * 0.22, cell * 0.28);
  } else if (item.kind === "couch") {
    drawFloorShadow(ctx, x + cell * 1.28, y + cell * 1.22, cell * 1.35, cell * 0.16);
    ctx.fillStyle = "#4a3f36";
    roundRect(ctx, x, y, cell * 2.55, cell * 1.2, 6);
    ctx.fill();
    ctx.fillStyle = "#6b6258";
    ctx.fillRect(x + cell * 0.22, y + cell * 0.18, cell * 0.95, cell * 0.55);
    ctx.fillRect(x + cell * 1.28, y + cell * 0.18, cell * 0.95, cell * 0.55);
    ctx.fillStyle = "#3a322b";
    ctx.fillRect(x, y, cell * 0.2, cell * 1.2);
    ctx.fillRect(x + cell * 2.35, y, cell * 0.2, cell * 1.2);
  } else if (item.kind === "plant") {
    drawPlant(ctx, x + cell * 0.45, y + cell * 0.4, cell * 0.055);
  } else if (item.kind === "lamp") {
    ctx.fillStyle = "#e6d39a";
    ctx.beginPath();
    ctx.arc(x + cell * 0.28, y + cell * 0.22, cell * 0.16, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + cell * 0.24, y + cell * 0.36, cell * 0.08, cell * 0.32);
  } else if (item.kind === "rug") {
    ctx.fillStyle = "#5a3a2a";
    ctx.fillRect(x, y, cell * 5, cell * 3);
    ctx.strokeStyle = "#c4a574";
    ctx.strokeRect(x + cell * 0.1, y + cell * 0.1, cell * 4.8, cell * 2.8);
  } else if (item.kind === "shelf") {
    ctx.fillStyle = "#6b5340";
    ctx.fillRect(x, y, cell * 2, cell * 0.22);
    ctx.fillStyle = "#8a6d4e";
    ctx.fillRect(x + cell * 0.12, y - cell * 0.32, cell * 0.28, cell * 0.32);
    ctx.fillRect(x + cell * 0.5, y - cell * 0.26, cell * 0.24, cell * 0.26);
    ctx.fillStyle = "#2a4d32";
    ctx.fillRect(x + cell * 0.9, y - cell * 0.3, cell * 0.32, cell * 0.3);
  } else if (item.kind === "clock") {
    ctx.fillStyle = "#f4efe4";
    ctx.beginPath();
    ctx.arc(x + cell * 0.32, y + cell * 0.32, cell * 0.26, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#1d1d1f";
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + cell * 0.32, y + cell * 0.32);
    ctx.lineTo(x + cell * 0.32, y + cell * 0.14);
    ctx.moveTo(x + cell * 0.32, y + cell * 0.32);
    ctx.lineTo(x + cell * 0.48, y + cell * 0.4);
    ctx.stroke();
  } else if (item.kind === "filing_cabinet") {
    ctx.fillStyle = "#6e6e73";
    ctx.fillRect(x, y, cell * 1.15, cell * 1.5);
    ctx.fillStyle = "#3a3a3c";
    ctx.fillRect(x + cell * 0.12, y + cell * 0.18, cell * 0.9, cell * 0.22);
    ctx.fillRect(x + cell * 0.12, y + cell * 0.52, cell * 0.9, cell * 0.22);
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(x + cell * 0.88, y + cell * 0.24, cell * 0.1, cell * 0.1);
  } else if (item.kind === "table") {
    drawFloorShadow(ctx, x + cell * 1.6, y + cell * 1.78, cell * 1.55, cell * 0.2);
    ctx.fillStyle = "#8a6d4e";
    roundRect(ctx, x, y, cell * 3.2, cell * 1.7, 6);
    ctx.fill();
    ctx.fillStyle = "#c4a574";
    ctx.fillRect(x + cell * 0.12, y + cell * 0.1, cell * 2.96, cell * 0.12);
    ctx.fillStyle = "#4a3a2c";
    ctx.fillRect(x + cell * 0.2, y + cell * 1.7, cell * 0.16, cell * 0.28);
    ctx.fillRect(x + cell * 2.84, y + cell * 1.7, cell * 0.16, cell * 0.28);
  } else if (item.kind === "beanbag") {
    ctx.fillStyle = "#4b5563";
    ctx.beginPath();
    ctx.ellipse(x + cell * 0.55, y + cell * 0.48, cell * 0.55, cell * 0.36, -0.15, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6366F1";
    ctx.beginPath();
    ctx.ellipse(x + cell * 0.55, y + cell * 0.32, cell * 0.32, cell * 0.16, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "minifridge") {
    ctx.fillStyle = "#e8e4dc";
    roundRect(ctx, x, y, cell * 0.55, cell * 0.85, 3);
    ctx.fill();
    ctx.fillStyle = "#8e8e93";
    ctx.fillRect(x + cell * 0.4, y + cell * 0.28, cell * 0.08, cell * 0.2);
    ctx.fillStyle = "#c4b8a8";
    ctx.fillRect(x + cell * 0.08, y + cell * 0.1, cell * 0.36, cell * 0.06);
  }
  const ad = item.advertises;
  if (ad) {
    ctx.font = "9px system-ui, sans-serif";
    const tw = ctx.measureText(ad).width;
    ctx.fillStyle = "rgba(26,20,16,0.82)";
    roundRect(ctx, x, y - 13, tw + 10, 11, 2);
    ctx.fill();
    ctx.fillStyle = "#f3ead7";
    ctx.fillText(ad, x + 5, y - 5);
  }
}

export function drawPerson(ctx, { employee, sprite, ox, oy, cell, now, hover, feel, verb }) {
  const px = ox + sprite.x * cell + cell * 0.35;
  const py = oy + sprite.y * cell + cell * 0.15;
  const color = employee.accent || employee.color || "#f3ead7";
  const look = lookOf(employee);
  const sitting = sprite.pose === "sit" || sprite.pose === "sit-type" || sprite.pose === "sit-talk";
  const standing = sprite.pose === "stand" || sprite.pose === "stand-talk";
  const bounce =
    sprite.pose === "walk"
      ? (sprite.frame ? cell * 0.11 : 0)
      : sitting || standing
        ? 0
        : sprite.pose === "idle"
          ? Math.sin(now / 420 + sprite.x) * cell * 0.04
          : 0;
  const s = cell * 0.118;

  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.55 + sprite.active * 0.35;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.ellipse(px, py + cell * (sitting ? 0.82 : 0.98) + bounce, cell * (sitting ? 0.34 : 0.28), cell * 0.09, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.lineWidth = 1;
  if (sprite.active > 0.08) {
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.22 + sprite.active * 0.4;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(px, py + bounce, cell * 0.78, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;
  }

  ctx.fillStyle = "rgba(0,0,0,0.4)";
  ctx.beginPath();
  ctx.ellipse(px, py + cell * (sitting ? 0.82 : 0.98) + bounce, cell * (sitting ? 0.42 : 0.36), cell * 0.12, 0, 0, Math.PI * 2);
  ctx.fill();

  const walk = sprite.pose === "walk" ? sprite.frame : 0;
  const type = sprite.pose === "type" || sprite.pose === "sit-type" ? sprite.frame : 0;
  const talk = sprite.pose === "talk" || sprite.pose === "sit-talk" || sprite.pose === "stand-talk";
  const blink = now < (sprite.blinkUntil || 0);

  ctx.save();
  ctx.translate(px, py + bounce + (sitting ? cell * 0.18 : 0));
  ctx.scale((sprite.facing || 1) * s, s);

  ctx.fillStyle = look.bottom;
  if (sitting) {
    if (look.bottomKind === "skirt") {
      ctx.beginPath();
      ctx.moveTo(-8, 12);
      ctx.lineTo(8, 12);
      ctx.lineTo(12, 20);
      ctx.lineTo(-12, 20);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.fillRect(-9, 13, 18, 7);
    }
    ctx.fillRect(-9, 18, 5, 6);
    ctx.fillRect(5, 18, 5, 6);
  } else if (look.bottomKind === "skirt") {
    ctx.beginPath();
    ctx.moveTo(-7, 12);
    ctx.lineTo(7, 12);
    ctx.lineTo(10, 24);
    ctx.lineTo(-10, 24);
    ctx.closePath();
    ctx.fill();
  } else {
    ctx.fillRect(-7, 13, 6, 12 + (walk ? 3 : 0) + (standing ? 1 : 0));
    ctx.fillRect(1, 13, 6, 12 + (walk ? 0 : 3) + (standing ? 1 : 0));
  }

  ctx.fillStyle = look.top;
  roundRect(ctx, -10, 0, 20, 16, 4);
  ctx.fill();
  if (look.topKind === "hoodie") {
    ctx.fillRect(-11, -3, 5, 9);
    ctx.fillRect(6, -3, 5, 9);
    ctx.fillStyle = "#ea580c";
    ctx.fillRect(-5, 5, 10, 6);
  } else if (look.topKind === "blazer") {
    ctx.fillStyle = "#b45309";
    ctx.fillRect(-10, 0, 4, 16);
    ctx.fillRect(6, 0, 4, 16);
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(-3, 1, 6, 10);
  } else if (look.topKind === "cardigan") {
    ctx.fillStyle = "#312e81";
    ctx.fillRect(-2, 0, 4, 16);
    ctx.fillStyle = "#e8e0d2";
    ctx.fillRect(-5, 3, 10, 8);
  } else if (look.topKind === "turtleneck") {
    ctx.fillRect(-6, -4, 12, 5);
  } else if (look.topKind === "flannel") {
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(-8, 4, 16, 2);
    ctx.fillRect(-8, 9, 16, 2);
  } else {
    ctx.fillStyle = "#14B8A6";
    ctx.fillRect(-4, 10, 8, 3);
  }

  const arm = sprite.pose === "type" ? -7 - type * 3 : sprite.pose === "walk" ? walk * 5 - 2 : talk ? -10 : 3;
  ctx.fillStyle = look.skin;
  ctx.fillRect(-13, 1, 5, 11 + (talk ? 2 : 0));
  ctx.fillRect(8, arm, 5, 11);
  if (talk) ctx.fillRect(9, -8, 4, 8);

  ctx.fillStyle = look.shoes;
  if (sitting) {
    ctx.fillRect(-9, 22, 6, 4);
    ctx.fillRect(5, 22, 6, 4);
  } else {
    ctx.fillRect(-7, 24, 6, 5);
    ctx.fillRect(1, 24, 6, 5);
  }

  ctx.fillStyle = look.skin;
  if (look.face === "square-crop") {
    roundRect(ctx, -8.2, -17.2, 16.4, 16.6, 3.2);
    ctx.fill();
  } else if (look.face === "long-part") {
    ctx.beginPath();
    ctx.ellipse(0, -9.4, 7.2, 9.4, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (look.face === "oval-pony") {
    ctx.beginPath();
    ctx.ellipse(0, -9.2, 7.6, 8.8, 0, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.arc(0, -8.6, 9, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.fillStyle = look.hair;
  if (look.hairStyle === "bun") {
    ctx.beginPath();
    ctx.arc(0, -16, 4.5, 0, Math.PI * 2);
    ctx.arc(0, -11, 8, Math.PI, 0);
    ctx.fill();
  } else if (look.hairStyle === "ponytail_dark" || look.face === "oval-pony") {
    ctx.beginPath();
    ctx.ellipse(0, -14.2, 8.6, 6.2, 0, Math.PI, 0.2);
    ctx.fill();
    ctx.fillRect(-8.4, -15, 16.8, 4.2);
    ctx.beginPath();
    ctx.moveTo(5.4, -14);
    ctx.quadraticCurveTo(18, -10, 17, 10);
    ctx.quadraticCurveTo(12, 12, 9, 6);
    ctx.quadraticCurveTo(8, -2, 6, -10);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#2a1a12";
    ctx.fillRect(-6.8, -16, 6.4, 3);
  } else if (look.hairStyle === "shoulder_brown" || look.hairStyle === "long_wave" || look.face === "long-part") {
    ctx.beginPath();
    ctx.ellipse(0, -13.6, 8.8, 6.4, 0, Math.PI, 0);
    ctx.fill();
    ctx.beginPath();
    ctx.ellipse(-8.4, -2, 4.4, 12, -0.18, 0, Math.PI * 2);
    ctx.ellipse(8.4, -2, 4.4, 12, 0.18, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#4a2a16";
    ctx.fillRect(-1.2, -16.2, 2.2, 5);
  } else if (look.hairStyle === "short_black_wavy" || look.face === "round-wave") {
    ctx.beginPath();
    ctx.arc(-5.2, -14.6, 4.4, 0, Math.PI * 2);
    ctx.arc(0.4, -16.2, 4.8, 0, Math.PI * 2);
    ctx.arc(5.6, -14.2, 4.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillRect(-9.4, -12, 5.2, 7);
    ctx.fillRect(4.6, -12, 5.2, 6);
  } else if (look.face === "square-crop") {
    ctx.fillRect(-8.6, -18.4, 17.2, 6.4);
    ctx.fillRect(-8.6, -16, 3.2, 5);
    ctx.fillRect(5.4, -16, 3.2, 5);
  } else {
    ctx.beginPath();
    ctx.arc(0, -12, 7.6, Math.PI, 0);
    ctx.fill();
  }

  if (blink) {
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(-4.2, -9.2);
    ctx.lineTo(-1.2, -9.2);
    ctx.moveTo(1.2, -9.2);
    ctx.lineTo(4.2, -9.2);
    ctx.stroke();
    ctx.lineWidth = 1;
  } else if (look.face === "square-crop") {
    ctx.fillStyle = "#1b1b1b";
    ctx.fillRect(-4.4, -10.6, 3.2, 2.8);
    ctx.fillRect(1.2, -10.6, 3.2, 2.8);
  } else if (look.face === "long-part") {
    ctx.fillStyle = "#3a2418";
    ctx.beginPath();
    ctx.ellipse(-2.8, -9.6, 1.5, 2.1, 0, 0, Math.PI * 2);
    ctx.ellipse(2.8, -9.6, 1.5, 2.1, 0, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.fillStyle = "#1b1b1b";
    ctx.fillRect(-3.5, -10, 2.4, 2.4);
    ctx.fillRect(1.1, -10, 2.4, 2.4);
  }
  if (talk) {
    ctx.fillStyle = "#5b2a2a";
    ctx.fillRect(-2, -4.5, 4, 2.4);
  }

  if (look.accessory === "glasses") {
    ctx.strokeStyle = "#1b1b1b";
    ctx.lineWidth = 1.2;
    ctx.strokeRect(-5, -11, 4.5, 3.4);
    ctx.strokeRect(0.5, -11, 4.5, 3.4);
    ctx.beginPath();
    ctx.moveTo(-0.5, -9.4);
    ctx.lineTo(0.5, -9.4);
    ctx.stroke();
  }
  if (look.accessory === "earbuds") {
    ctx.fillStyle = "#eee";
    ctx.beginPath();
    ctx.arc(-7, -8, 1.8, 0, Math.PI * 2);
    ctx.fill();
  }
  if (look.accessory === "watch") {
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(-13, 7, 5, 2.4);
  }
  if (look.accessory === "keys") {
    ctx.fillStyle = "#d4af37";
    ctx.beginPath();
    ctx.arc(10, 14, 2.6, 0, Math.PI * 2);
    ctx.fill();
  }
  if (look.accessory === "badge") {
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(4, 4, 5, 6);
  }
  if (look.accessory === "scarf") {
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(-8, 0, 16, 3);
  }
  ctx.restore();

  const first = employee.name.split(" ")[0];
  ctx.font = `600 ${Math.max(12, Math.round(cell * 0.34))}px system-ui, sans-serif`;
  const plate = `${first}  ${employee.role}`;
  const tw = ctx.measureText(plate).width;
  const plateY = py + cell * 1.05;
  ctx.fillStyle = "#1a1410";
  roundRect(ctx, px - tw / 2 - 8, plateY, tw + 16, cell * 0.38, 3);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillRect(px - tw / 2 - 8, plateY, 4, cell * 0.38);
  ctx.fillStyle = "#f3ead7";
  ctx.fillText(plate, px - tw / 2, plateY + cell * 0.27);

  if (verb) {
    ctx.font = `${Math.max(10, Math.round(cell * 0.26))}px system-ui, sans-serif`;
    const vw = ctx.measureText(verb).width;
    const verbY = py - cell * 0.85;
    ctx.fillStyle = "rgba(26,20,16,0.8)";
    roundRect(ctx, px - vw / 2 - 6, verbY, vw + 12, cell * 0.3, 2);
    ctx.fill();
    ctx.fillStyle = "#c4b8a8";
    ctx.fillText(verb, px - vw / 2, verbY + cell * 0.22);
  }

  if (hover) {
    const chip = `${employee.name.split(" ")[0]} · ${employee.model || employee.modelFamily || ""}`;
    ctx.font = `${Math.max(10, Math.round(cell * 0.28))}px system-ui, sans-serif`;
    const cw = ctx.measureText(chip).width;
    ctx.fillStyle = "#1a1410";
    roundRect(ctx, px - 8, py - cell * 1.2, cw + 16, cell * 0.36, 3);
    ctx.fill();
    ctx.fillStyle = "#f3ead7";
    ctx.fillText(chip, px, py - cell * 0.94);
    if (feel) {
      ctx.fillStyle = "#c4b8a8";
      ctx.fillText(feel, px - 8, py - cell * 1.32);
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
  const x = ox + sprite.x * cell + 20;
  const y = oy + sprite.y * cell - 42;
  ctx.save();
  ctx.globalAlpha = Math.max(0, alpha);
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  const lines = bubble.lines;
  ctx.font = "12px system-ui, sans-serif";
  const w = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 18;
  const h = 14 + lines.length * 14;
  ctx.fillStyle = "#f3ead7";
  roundRect(ctx, 0, 0, w, h, 6);
  ctx.fill();
  ctx.strokeStyle = bubble.color || "#c4b8a8";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(10, h);
  ctx.lineTo(5, h + 8);
  ctx.lineTo(18, h);
  ctx.fill();
  ctx.fillStyle = "#1a1410";
  lines.forEach((line, i) => ctx.fillText(line, 9, 16 + i * 14));
  ctx.restore();
  return true;
}
