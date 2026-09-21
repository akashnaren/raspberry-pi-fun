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

export function lookOf(employee) {
  const aesthetics = employee.aesthetics || {};
  const outfit = aesthetics.outfit || {};
  return {
    skin: SKIN[aesthetics.skin] || SKIN.warm_medium,
    hair: HAIR_COLOR[aesthetics.hair] || employee.color || "#1b1b1b",
    hairStyle: aesthetics.hair || "short_black_wavy",
    top: TOP_COLOR[outfit.top] || employee.color || "#888",
    topKind: outfit.top || "tee",
    bottom: BOTTOM_COLOR[outfit.bottom] || "#314e73",
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
  ctx.fillStyle = ROOM_VOID;
  ctx.fillRect(0, 0, w, h);
}

function floorTone(room) {
  if (/break/i.test(room.name)) return { a: "#5a4636", b: "#4a3a2c", grout: "#3a2c22" };
  if (/meeting|lab/i.test(room.name)) return { a: "#4a4e55", b: "#3f4349", grout: "#2c3036" };
  return { a: "#6b5340", b: "#5a4534", grout: "#3d2e22" };
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

function drawWindow(ctx, x, y, w, h, dim) {
  const glow = ctx.createLinearGradient(x, y, x, y + h + 40);
  glow.addColorStop(0, dim ? "rgba(255,196,110,0.10)" : "rgba(255,196,110,0.28)");
  glow.addColorStop(1, "rgba(255,196,110,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(x - 6, y, w + 12, h + 48);
  ctx.fillStyle = "#1a2230";
  roundRect(ctx, x, y, w, h, 2);
  ctx.fill();
  ctx.fillStyle = dim ? "#3d4a3a" : "#6b8a6a";
  ctx.fillRect(x + 3, y + 3, w / 2 - 5, h - 6);
  ctx.fillStyle = dim ? "#2e3a48" : "#7a9aaa";
  ctx.fillRect(x + w / 2 + 1, y + 3, w / 2 - 5, h - 6);
  ctx.strokeStyle = "#c4b08a";
  ctx.lineWidth = 2;
  ctx.strokeRect(x + 1, y + 1, w - 2, h - 2);
  ctx.beginPath();
  ctx.moveTo(x + w / 2, y + 2);
  ctx.lineTo(x + w / 2, y + h - 2);
  ctx.stroke();
  ctx.lineWidth = 1;
}

function drawPlant(ctx, x, y, scale, sway = 0) {
  ctx.fillStyle = "#6b3a22";
  ctx.fillRect(x - 4 * scale, y + 8 * scale, 8 * scale, 7 * scale);
  ctx.fillStyle = "#3d6b46";
  ctx.beginPath();
  ctx.ellipse(x - 5 * scale + sway, y + 1 * scale, 6 * scale, 5 * scale, -0.4, 0, Math.PI * 2);
  ctx.ellipse(x + 5 * scale + sway, y + 1 * scale, 6 * scale, 5 * scale, 0.4, 0, Math.PI * 2);
  ctx.ellipse(x + sway, y - 4 * scale, 5 * scale, 6 * scale, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#2a4d32";
  ctx.beginPath();
  ctx.ellipse(x + sway, y + 2 * scale, 4 * scale, 4 * scale, 0, 0, Math.PI * 2);
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
  ctx.fillRect(ox - 18, oy - 18, bounds.w * cell + 36, bounds.h * cell + 36);
  ctx.fillStyle = wall;
  ctx.fillRect(ox - 10, oy - 14, bounds.w * cell + 20, bounds.h * cell + 28);

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

  for (const lamp of office.decor || []) {
    if (lamp.kind !== "lamp") continue;
    const lx = ox + lamp.x * cell + cell * 0.35;
    const ly = oy + lamp.y * cell + cell * 0.25;
    const glow = ctx.createRadialGradient(lx, ly, 2, lx, ly, cell * 2.4);
    glow.addColorStop(0, dim ? "rgba(255,196,110,0.16)" : "rgba(255,210,130,0.38)");
    glow.addColorStop(1, "rgba(255,196,110,0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(lx, ly, cell * 2.4, 0, Math.PI * 2);
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

function drawDesk(ctx, ox, oy, cell, desk, employees) {
  const x = ox + desk.x * cell;
  const y = oy + desk.y * cell;
  const west = desk.facing === "west";
  ctx.fillStyle = "rgba(0,0,0,0.28)";
  ctx.beginPath();
  ctx.ellipse(x + cell * 1.1, y + cell * 1.55, cell * 1.15, 6, 0, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = "#3a2a20";
  ctx.fillRect(x + 6, y + cell * 1.15, 5, cell * 0.35);
  ctx.fillRect(x + cell * 1.85, y + cell * 1.15, 5, cell * 0.35);
  ctx.fillStyle = "#8a6d4e";
  roundRect(ctx, x, y, cell * 2.2, cell * 1.22, 4);
  ctx.fill();
  ctx.fillStyle = "#a88862";
  ctx.fillRect(x + 3, y + 3, cell * 2.2 - 6, 4);
  ctx.fillStyle = "#6b5340";
  ctx.fillRect(x + 4, y + cell * 1.05, cell * 2.2 - 8, 3);

  ctx.fillStyle = "#4a3a2c";
  roundRect(ctx, x + (west ? cell * 1.35 : 8), y + cell * 1.28, cell * 0.72, cell * 0.42, 3);
  ctx.fill();
  ctx.fillStyle = "#2a2118";
  ctx.fillRect(x + (west ? cell * 1.5 : 14), y + cell * 1.18, 8, 6);

  const items = desk.items || [];
  if (items.includes("monitor") || items.includes("second_monitor")) {
    ctx.fillStyle = "#1b1b1d";
    ctx.fillRect(x + 10, y + 6, 26, 16);
    const screen = ctx.createLinearGradient(x + 12, y + 8, x + 32, y + 20);
    screen.addColorStop(0, "#d7ecff");
    screen.addColorStop(1, "#8ab0c8");
    ctx.fillStyle = screen;
    ctx.fillRect(x + 12, y + 8, 22, 12);
    ctx.fillStyle = "#2c2c2e";
    ctx.fillRect(x + 20, y + 22, 6, 5);
    if (items.includes("second_monitor")) {
      ctx.fillStyle = "#1b1b1d";
      ctx.fillRect(x + 38, y + 8, 18, 13);
      ctx.fillStyle = "#c5d8c8";
      ctx.fillRect(x + 40, y + 10, 14, 9);
    }
  }
  if (items.includes("plant")) drawPlant(ctx, x + cell * 1.85, y + 10, 0.85);
  if (items.includes("coffee_mug")) {
    ctx.fillStyle = "#c4b8a8";
    ctx.fillRect(x + 8, y + 22, 8, 7);
    ctx.strokeStyle = "#c4b8a8";
    ctx.beginPath();
    ctx.arc(x + 16, y + 25, 2.5, -0.6, 0.6);
    ctx.stroke();
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + 9, y + 23, 6, 3);
  }
  if (items.includes("notebook") || items.includes("sticky_notes")) {
    ctx.fillStyle = items.includes("sticky_notes") ? "#e6d36a" : "#f2efe8";
    ctx.fillRect(x + 44, y + 22, 12, 9);
  }
  const owner = (employees || []).find((person) => person.id === desk.owner);
  const style = owner?.aesthetics?.desk_style || "";
  if (/messy|cable|sticker/i.test(style)) {
    ctx.strokeStyle = "#8e8e93";
    ctx.beginPath();
    ctx.moveTo(x + 8, y + 24);
    ctx.quadraticCurveTo(x + 20, y + 30, x + 32, y + 24);
    ctx.stroke();
    ctx.fillStyle = "#F97316";
    ctx.fillRect(x + 30, y + 20, 6, 5);
  }
  const tag = (owner?.name || desk.owner).split(" ")[0];
  ctx.font = "10px system-ui, sans-serif";
  const tw = ctx.measureText(tag).width;
  ctx.fillStyle = "#1a1410";
  roundRect(ctx, x + 4, y - 14, tw + 12, 12, 3);
  ctx.fill();
  ctx.fillStyle = owner?.accent || "#f3ead7";
  ctx.fillRect(x + 4, y - 14, 3, 12);
  ctx.fillStyle = "#f3ead7";
  ctx.fillText(tag, x + 10, y - 5);
}

function drawDecor(ctx, ox, oy, cell, item, dim) {
  const x = ox + item.x * cell;
  const y = oy + item.y * cell;
  if (item.kind === "window") {
    drawWindow(ctx, x, y, (item.w || 2) * cell, (item.h || 1) * cell * 0.85, dim);
  } else if (item.kind === "whiteboard") {
    const w = (item.w || 6) * cell;
    ctx.fillStyle = "#2a241c";
    ctx.fillRect(x - 3, y - 3, w + 6, cell * 1.15);
    ctx.fillStyle = "#f4efe4";
    ctx.fillRect(x, y, w, cell * 1.02);
    ctx.fillStyle = "#1d1d1f";
    ctx.font = `600 ${Math.max(10, Math.round(cell * 0.34))}px system-ui, sans-serif`;
    const words = String(item.text || "SHIP").split(" ");
    let line = "";
    let row = 0;
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (ctx.measureText(next).width > w - 12) {
        ctx.fillText(line, x + 6, y + 16 + row * 13);
        line = word;
        row += 1;
        if (row > 1) break;
      } else {
        line = next;
      }
    }
    if (line && row < 2) ctx.fillText(line, x + 6, y + 16 + row * 13);
  } else if (item.kind === "coffee") {
    ctx.fillStyle = "#2c2c2e";
    roundRect(ctx, x, y, cell * 1.15, cell * 1.2, 3);
    ctx.fill();
    ctx.fillStyle = "#d2d2d7";
    ctx.fillRect(x + 6, y + 5, 18, 8);
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(x + 8, y + 16, 10, 4);
    ctx.fillStyle = "#c4b8a8";
    ctx.fillRect(x + 22, y + 22, 7, 8);
  } else if (item.kind === "couch") {
    ctx.fillStyle = "#4a3f36";
    roundRect(ctx, x, y, cell * 2.5, cell * 1.15, 6);
    ctx.fill();
    ctx.fillStyle = "#6b6258";
    ctx.fillRect(x + 8, y + 6, cell * 0.95, cell * 0.55);
    ctx.fillRect(x + cell * 1.2, y + 6, cell * 0.95, cell * 0.55);
    ctx.fillStyle = "#3a322b";
    ctx.fillRect(x, y, 8, cell * 1.15);
    ctx.fillRect(x + cell * 2.25, y, 8, cell * 1.15);
  } else if (item.kind === "plant") {
    drawPlant(ctx, x + 12, y + 10, 1.15);
  } else if (item.kind === "lamp") {
    ctx.fillStyle = "#e6d39a";
    ctx.beginPath();
    ctx.arc(x + 8, y + 6, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#3a2a20";
    ctx.fillRect(x + 7, y + 10, 2, 10);
  } else if (item.kind === "rug") {
    ctx.fillStyle = "#5a3a2a";
    ctx.fillRect(x, y, cell * 5, cell * 3);
    ctx.strokeStyle = "#c4a574";
    ctx.strokeRect(x + 3, y + 3, cell * 5 - 6, cell * 3 - 6);
  } else if (item.kind === "shelf") {
    ctx.fillStyle = "#6b5340";
    ctx.fillRect(x, y, cell * 2, 7);
    ctx.fillStyle = "#8a6d4e";
    ctx.fillRect(x + 4, y - 10, 8, 10);
    ctx.fillRect(x + 16, y - 8, 7, 8);
    ctx.fillStyle = "#2a4d32";
    ctx.fillRect(x + 28, y - 9, 10, 9);
  } else if (item.kind === "clock") {
    ctx.fillStyle = "#f4efe4";
    ctx.beginPath();
    ctx.arc(x + 10, y + 10, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#1d1d1f";
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x + 10, y + 10);
    ctx.lineTo(x + 10, y + 5);
    ctx.moveTo(x + 10, y + 10);
    ctx.lineTo(x + 14, y + 12);
    ctx.stroke();
  } else if (item.kind === "filing_cabinet") {
    ctx.fillStyle = "#6e6e73";
    ctx.fillRect(x, y, cell * 1.1, cell * 1.45);
    ctx.fillStyle = "#3a3a3c";
    ctx.fillRect(x + 4, y + 6, cell * 0.8, 7);
    ctx.fillRect(x + 4, y + 18, cell * 0.8, 7);
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(x + cell * 0.85, y + 8, 3, 3);
  } else if (item.kind === "table") {
    ctx.fillStyle = "rgba(0,0,0,0.22)";
    ctx.beginPath();
    ctx.ellipse(x + cell * 1.6, y + cell * 1.7, cell * 1.5, 6, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#8a6d4e";
    roundRect(ctx, x, y, cell * 3.2, cell * 1.65, 6);
    ctx.fill();
    ctx.fillStyle = "#a88862";
    ctx.fillRect(x + 6, y + 4, cell * 3.2 - 12, 4);
    ctx.fillStyle = "#4a3a2c";
    ctx.fillRect(x + 8, y + cell * 1.7, 6, 8);
    ctx.fillRect(x + cell * 2.8, y + cell * 1.7, 6, 8);
  } else if (item.kind === "beanbag") {
    ctx.fillStyle = "#4b5563";
    ctx.beginPath();
    ctx.ellipse(x + 16, y + 14, 17, 11, -0.15, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6366F1";
    ctx.beginPath();
    ctx.ellipse(x + 16, y + 10, 10, 5, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "minifridge") {
    ctx.fillStyle = "#e8e4dc";
    roundRect(ctx, x, y, 16, 24, 2);
    ctx.fill();
    ctx.fillStyle = "#8e8e93";
    ctx.fillRect(x + 12, y + 8, 2, 6);
    ctx.fillStyle = "#c4b8a8";
    ctx.fillRect(x + 3, y + 3, 10, 2);
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
  const px = ox + sprite.x * cell;
  const py = oy + sprite.y * cell;
  const color = employee.accent || employee.color || "#f3ead7";
  const look = lookOf(employee);
  const bounce =
    sprite.pose === "walk"
      ? (sprite.frame ? 2 : 0)
      : sprite.pose === "idle"
        ? Math.sin(now / 420 + sprite.x) * 1.1
        : 0;
  const s = Math.max(0.85, cell / 28);

  if (sprite.active > 0.08) {
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.2 + sprite.active * 0.4;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(px + 9, py + 6 + bounce, 19 * s, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;
  }

  ctx.fillStyle = "rgba(0,0,0,0.28)";
  ctx.beginPath();
  ctx.ellipse(px + 9, py + 28 * s, 9 * s, 3.2 * s, 0, 0, Math.PI * 2);
  ctx.fill();

  const walk = sprite.pose === "walk" ? sprite.frame : 0;
  const type = sprite.pose === "type" ? sprite.frame : 0;
  const talk = sprite.pose === "talk";
  const blink = now < (sprite.blinkUntil || 0);

  ctx.save();
  ctx.translate(px + 9, py + 6 + bounce);
  ctx.scale((sprite.facing || 1) * s, s);

  ctx.fillStyle = look.bottom;
  if (look.topKind && employee.aesthetics?.outfit?.bottom === "skirt") {
    ctx.beginPath();
    ctx.moveTo(-6, 10);
    ctx.lineTo(6, 10);
    ctx.lineTo(8, 20);
    ctx.lineTo(-8, 20);
    ctx.closePath();
    ctx.fill();
  } else {
    ctx.fillRect(-6, 11, 5, 10 + (walk ? 2 : 0));
    ctx.fillRect(1, 11, 5, 10 + (walk ? 0 : 2));
  }

  ctx.fillStyle = look.top;
  roundRect(ctx, -8, 0, 16, 13, 3);
  ctx.fill();
  if (look.topKind === "hoodie") {
    ctx.fillRect(-9, -2, 4, 7);
    ctx.fillRect(5, -2, 4, 7);
    ctx.fillStyle = "#ea580c";
    ctx.fillRect(-4, 4, 8, 5);
  } else if (look.topKind === "blazer") {
    ctx.fillStyle = "#b45309";
    ctx.fillRect(-8, 0, 3, 13);
    ctx.fillRect(5, 0, 3, 13);
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(-2, 0, 4, 8);
  } else if (look.topKind === "cardigan") {
    ctx.fillStyle = "#312e81";
    ctx.fillRect(-1, 0, 2, 13);
    ctx.fillStyle = "#e8e0d2";
    ctx.fillRect(-4, 2, 8, 6);
  } else if (look.topKind === "turtleneck") {
    ctx.fillRect(-5, -3, 10, 4);
  } else if (look.topKind === "flannel") {
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(-7, 3, 14, 1);
    ctx.fillRect(-7, 7, 14, 1);
  } else {
    ctx.fillStyle = "#14B8A6";
    ctx.fillRect(-3, 8, 6, 2);
  }

  const arm = sprite.pose === "type" ? -6 - type * 3 : sprite.pose === "walk" ? walk * 4 - 2 : talk ? -8 : 2;
  ctx.fillStyle = look.skin;
  ctx.fillRect(-11, 1, 4, 9 + (talk ? 2 : 0));
  ctx.fillRect(7, arm, 4, 9);
  if (talk) {
    ctx.fillRect(8, -6, 3, 6);
  }

  ctx.fillStyle = look.shoes;
  ctx.fillRect(-6, 20, 5, 4);
  ctx.fillRect(1, 20, 5, 4);

  ctx.fillStyle = look.skin;
  ctx.beginPath();
  ctx.arc(0, -8, 7.2, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = look.hair;
  if (look.hairStyle === "bun") {
    ctx.beginPath();
    ctx.arc(0, -14, 4, 0, Math.PI * 2);
    ctx.arc(0, -10, 7, Math.PI, 0);
    ctx.fill();
  } else if (look.hairStyle === "ponytail_dark") {
    ctx.beginPath();
    ctx.arc(0, -10, 7, Math.PI, 0.15);
    ctx.fill();
    ctx.fillRect(5, -12, 4, 12);
  } else if (look.hairStyle === "shoulder_brown" || look.hairStyle === "long_wave") {
    ctx.beginPath();
    ctx.arc(0, -10, 7.4, Math.PI, 0);
    ctx.fill();
    ctx.fillRect(-8, -10, 3, 10);
    ctx.fillRect(5, -10, 3, 10);
  } else if (look.hairStyle === "short_black_wavy") {
    ctx.beginPath();
    ctx.arc(0, -10, 7.4, Math.PI, 0.2);
    ctx.fill();
    ctx.fillRect(-8, -9, 3, 5);
  } else {
    ctx.beginPath();
    ctx.arc(0, -11, 6.6, Math.PI, 0);
    ctx.fill();
  }

  if (blink) {
    ctx.strokeStyle = "#1b1b1b";
    ctx.beginPath();
    ctx.moveTo(-3, -8);
    ctx.lineTo(-1, -8);
    ctx.moveTo(1, -8);
    ctx.lineTo(3, -8);
    ctx.stroke();
  } else {
    ctx.fillStyle = "#1b1b1b";
    ctx.fillRect(-3, -9, 2, 2);
    ctx.fillRect(1, -9, 2, 2);
  }
  ctx.fillStyle = look.skin;
  if (talk) {
    ctx.fillStyle = "#5b2a2a";
    ctx.fillRect(-1.5, -4, 3, 2);
  }

  if (look.accessory === "glasses") {
    ctx.strokeStyle = "#1b1b1b";
    ctx.strokeRect(-4.5, -10, 4, 3);
    ctx.strokeRect(0.5, -10, 4, 3);
    ctx.beginPath();
    ctx.moveTo(-0.5, -8.5);
    ctx.lineTo(0.5, -8.5);
    ctx.stroke();
  }
  if (look.accessory === "earbuds") {
    ctx.fillStyle = "#eee";
    ctx.beginPath();
    ctx.arc(-6, -7, 1.6, 0, Math.PI * 2);
    ctx.fill();
  }
  if (look.accessory === "watch") {
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(-11, 6, 4, 2);
  }
  if (look.accessory === "keys") {
    ctx.fillStyle = "#d4af37";
    ctx.beginPath();
    ctx.arc(8, 12, 2.2, 0, Math.PI * 2);
    ctx.fill();
  }
  if (look.accessory === "badge") {
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(3, 3, 4, 5);
  }
  if (look.accessory === "scarf") {
    ctx.fillStyle = "#6366F1";
    ctx.fillRect(-7, 0, 14, 3);
  }
  ctx.restore();

  const first = employee.name.split(" ")[0];
  ctx.font = "11px system-ui, sans-serif";
  const plate = `${first}  ${employee.role}`;
  const tw = ctx.measureText(plate).width;
  ctx.fillStyle = "#1a1410";
  roundRect(ctx, px + 9 - tw / 2 - 6, py + 32, tw + 12, 13, 3);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillRect(px + 9 - tw / 2 - 6, py + 32, 3, 13);
  ctx.fillStyle = "#f3ead7";
  ctx.fillText(plate, px + 9 - tw / 2, py + 42);

  if (verb) {
    ctx.font = "9px system-ui, sans-serif";
    const vw = ctx.measureText(verb).width;
    ctx.fillStyle = "rgba(26,20,16,0.78)";
    roundRect(ctx, px + 9 - vw / 2 - 4, py - 20, vw + 8, 11, 2);
    ctx.fill();
    ctx.fillStyle = "#c4b8a8";
    ctx.fillText(verb, px + 9 - vw / 2, py - 12);
  }

  if (hover) {
    const chip = `${employee.name.split(" ")[0]} · ${employee.model || employee.modelFamily || ""}`;
    ctx.font = "10px system-ui, sans-serif";
    const cw = ctx.measureText(chip).width;
    ctx.fillStyle = "#1a1410";
    roundRect(ctx, px - 6, py - 36, cw + 12, 14, 3);
    ctx.fill();
    ctx.fillStyle = "#f3ead7";
    ctx.fillText(chip, px, py - 26);
    if (feel) {
      ctx.fillStyle = "#c4b8a8";
      ctx.fillText(feel, px - 6, py - 40);
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
