export const TOKENS = {
  floor: "#1A1F2E",
  floorMid: "#232A3B",
  wall: "#2C3348",
  desk: "#3D465C",
  deskEdge: "#4A5568",
  sprite: "#2A3144",
  lampAmber: "#F5B942",
  lampCore: "#FFE08A",
  text: "#E8ECF4",
  mute: "#9AA3B5",
  nova: "#F97316",
  kessler: "#14B8A6",
  mira: "#F59E0B",
  live: "#22C55E",
  fail: "#EF4444",
};

export const FLOOR = { w: 800, h: 600, tile: 40 };

const ALIAS = {
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

export function propId(id) {
  return ALIAS[id] || id;
}

export function isNorm(office) {
  return office?.coord_space === "topdown_norm";
}

export function deskOwner(desk) {
  return desk?.owner || desk?.employee;
}

export function deskItems(desk) {
  if (Array.isArray(desk?.items) && desk.items.length) return desk.items;
  return (desk?.props || []).map((item) => item.id).filter(Boolean);
}

export function layout(canvas, dpr, office) {
  if (!isNorm(office)) {
    return { scale: 30, ox: 20, oy: 16, norm: false, w: 800, h: 600 };
  }
  const w = canvas.width / dpr;
  const h = canvas.height / dpr;
  const scale = Math.min(w / FLOOR.w, h / FLOOR.h);
  return {
    scale,
    ox: (w - FLOOR.w * scale) / 2,
    oy: (h - FLOOR.h * scale) / 2,
    norm: true,
    w: FLOOR.w,
    h: FLOOR.h,
  };
}

export function toPx(nx, ny, lay) {
  if (!lay.norm) return { x: lay.ox + nx * 30, y: lay.oy + ny * 30 };
  return { x: lay.ox + nx * FLOOR.w * lay.scale, y: lay.oy + ny * FLOOR.h * lay.scale };
}

export function standAtDesk(desk, office) {
  if (!desk) return isNorm(office) ? { x: 0.22, y: 0.48 } : { x: 4, y: 6 };
  if (desk.sprite) return { x: desk.sprite.x, y: desk.sprite.y };
  if (isNorm(office)) return { x: desk.x, y: desk.y + 0.1 };
  return { x: desk.x + 1, y: desk.y + 2 };
}

function round(ctx, x, y, w, h, r) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
  ctx.closePath();
}

function shadow(ctx, draw) {
  ctx.save();
  ctx.fillStyle = "rgba(0,0,0,0.28)";
  ctx.translate(2, 3);
  draw();
  ctx.fill();
  ctx.restore();
}

export function drawLampGlow(ctx, x, y, scale, dim) {
  const rx = 72 * scale;
  const ry = 42 * scale;
  const glow = ctx.createRadialGradient(x, y, 2, x, y, rx);
  glow.addColorStop(0, dim ? "rgba(255,224,138,0.10)" : "rgba(255,224,138,0.45)");
  glow.addColorStop(0.4, dim ? "rgba(245,185,66,0.08)" : "rgba(245,185,66,0.22)");
  glow.addColorStop(1, "rgba(245,185,66,0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
  ctx.fill();
}

export function drawProp(ctx, id, x, y, scale, extra = {}) {
  const kind = propId(id);
  const s = scale;
  ctx.save();
  ctx.translate(x, y);
  if (kind === "desk_pad") {
    shadow(ctx, () => round(ctx, -48 * s, -28 * s, 96 * s, 56 * s, 12 * s));
    ctx.fillStyle = TOKENS.desk;
    ctx.strokeStyle = TOKENS.deskEdge;
    round(ctx, -48 * s, -28 * s, 96 * s, 56 * s, 12 * s);
    ctx.fill();
    ctx.stroke();
  } else if (kind === "chair") {
    ctx.fillStyle = TOKENS.deskEdge;
    round(ctx, -14 * s, -14 * s, 28 * s, 28 * s, 6 * s);
    ctx.fill();
  } else if (kind === "monitor") {
    ctx.fillStyle = TOKENS.floor;
    ctx.strokeStyle = TOKENS.deskEdge;
    round(ctx, -18 * s, -11 * s, 36 * s, 22 * s, 3 * s);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = extra.accent || TOKENS.kessler;
    ctx.globalAlpha = 0.45;
    ctx.fillRect(-15 * s, -8 * s, 30 * s, 16 * s);
    ctx.globalAlpha = 1;
  } else if (kind === "keyboard") {
    ctx.fillStyle = TOKENS.sprite;
    round(ctx, -16 * s, -6 * s, 32 * s, 12 * s, 2 * s);
    ctx.fill();
  } else if (kind === "mug") {
    ctx.fillStyle = "#C45C3E";
    ctx.beginPath();
    ctx.arc(0, 0, 7 * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#C45C3E";
    ctx.beginPath();
    ctx.arc(8 * s, 0, 4 * s, -0.8, 0.8);
    ctx.stroke();
  } else if (kind === "plant_small" || kind === "plant_tall") {
    ctx.fillStyle = TOKENS.live;
    ctx.beginPath();
    ctx.ellipse(0, -4 * s, 8 * s, 10 * s, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6B3A22";
    ctx.fillRect(-4 * s, 4 * s, 8 * s, 6 * s);
  } else if (kind === "lamp_desk" || kind === "lamp_floor") {
    ctx.fillStyle = TOKENS.lampCore;
    ctx.beginPath();
    ctx.arc(0, 0, 5 * s, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = TOKENS.deskEdge;
    ctx.fillRect(-2 * s, 4 * s, 4 * s, 8 * s);
  } else if (kind === "notebook") {
    ctx.fillStyle = "#EFE6D4";
    ctx.fillRect(-8 * s, -10 * s, 16 * s, 20 * s);
  } else if (kind === "sticky_stack") {
    ctx.fillStyle = "#FDE68A";
    ctx.fillRect(-6 * s, -6 * s, 12 * s, 12 * s);
    ctx.fillRect(-3 * s, -8 * s, 12 * s, 12 * s);
  } else if (kind === "whiteboard") {
    shadow(ctx, () => round(ctx, -50 * s, -32 * s, 100 * s, 64 * s, 8 * s));
    ctx.fillStyle = TOKENS.desk;
    ctx.strokeStyle = TOKENS.deskEdge;
    round(ctx, -50 * s, -32 * s, 100 * s, 64 * s, 8 * s);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = TOKENS.text;
    round(ctx, -42 * s, -24 * s, 84 * s, 40 * s, 4 * s);
    ctx.fill();
    ctx.fillStyle = TOKENS.floor;
    ctx.font = `${10 * s}px ${extra.font || "sans-serif"}`;
    const text = String(extra.text || "SHIP");
    ctx.fillText(text.slice(0, 28), -38 * s, -8 * s);
    if (text.length > 28) ctx.fillText(text.slice(28, 56), -38 * s, 8 * s);
  } else if (kind === "coffee_machine") {
    ctx.fillStyle = TOKENS.desk;
    ctx.strokeStyle = TOKENS.deskEdge;
    round(ctx, -18 * s, -20 * s, 36 * s, 40 * s, 4 * s);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = TOKENS.lampAmber;
    ctx.fillRect(-10 * s, -12 * s, 20 * s, 10 * s);
  } else if (kind === "couch_2seat") {
    ctx.fillStyle = TOKENS.desk;
    round(ctx, -40 * s, -18 * s, 80 * s, 36 * s, 12 * s);
    ctx.fill();
  } else if (kind === "table_round") {
    ctx.fillStyle = TOKENS.desk;
    ctx.strokeStyle = TOKENS.deskEdge;
    ctx.beginPath();
    ctx.ellipse(0, 0, 22 * s, 22 * s, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  } else if (kind === "water_cooler") {
    ctx.fillStyle = TOKENS.desk;
    ctx.fillRect(-10 * s, -8 * s, 20 * s, 24 * s);
    ctx.fillStyle = "#38BDF8";
    ctx.beginPath();
    ctx.ellipse(0, -16 * s, 10 * s, 12 * s, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (kind === "filing_cabinet") {
    ctx.fillStyle = TOKENS.desk;
    ctx.fillRect(-14 * s, -20 * s, 28 * s, 40 * s);
  } else {
    ctx.fillStyle = TOKENS.desk;
    ctx.fillRect(-8 * s, -8 * s, 16 * s, 16 * s);
  }
  ctx.restore();
}

export function drawNormOffice(ctx, office, lay, dim, font) {
  const { ox, oy, scale } = lay;
  ctx.fillStyle = TOKENS.floor;
  ctx.fillRect(ox, oy, FLOOR.w * scale, FLOOR.h * scale);
  ctx.strokeStyle = "rgba(35,42,59,0.5)";
  ctx.lineWidth = 1;
  for (let g = 40; g < FLOOR.w; g += 40) {
    ctx.beginPath();
    ctx.moveTo(ox + g * scale, oy);
    ctx.lineTo(ox + g * scale, oy + FLOOR.h * scale);
    ctx.stroke();
  }
  for (let g = 40; g < FLOOR.h; g += 40) {
    ctx.beginPath();
    ctx.moveTo(ox, oy + g * scale);
    ctx.lineTo(ox + FLOOR.w * scale, oy + g * scale);
    ctx.stroke();
  }
  ctx.fillStyle = TOKENS.wall;
  ctx.fillRect(ox, oy, FLOOR.w * scale, 24 * scale);
  ctx.fillStyle = TOKENS.text;
  ctx.font = `700 ${14 * scale}px ${font}`;
  ctx.fillText("Meridian Desk", ox + 12 * scale, oy + 17 * scale);

  for (const zone of office.zones || []) {
    if (zone.prop !== "floor_break_tint") continue;
    const p = toPx(zone.x, zone.y, lay);
    ctx.fillStyle = TOKENS.floorMid;
    ctx.globalAlpha = 0.85;
    round(ctx, p.x, p.y, zone.w * FLOOR.w * scale, zone.h * FLOOR.h * scale, 16 * scale);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  for (const desk of office.desks || []) {
    const center = toPx(desk.x, desk.y, lay);
    drawLampGlow(ctx, center.x, center.y - 18 * scale, scale, dim);
  }

  for (const desk of office.desks || []) {
    const center = toPx(desk.x, desk.y, lay);
    drawProp(ctx, desk.prop || "desk_pad", center.x, center.y, scale);
    if (desk.chair) {
      drawProp(ctx, "chair", center.x + desk.chair.dx * scale, center.y + desk.chair.dy * scale, scale);
    }
    for (const item of desk.props || []) {
      drawProp(ctx, item.id, center.x + item.dx * scale, center.y + item.dy * scale, scale, {
        accent: desk.accent,
        font,
      });
    }
  }

  const objects = office.objects?.length ? office.objects : office.decor || [];
  for (const item of objects) {
    const p = toPx(item.x, item.y, lay);
    drawProp(ctx, item.prop || item.kind, p.x, p.y, scale, { text: item.text, font });
    if (item.advertises) {
      ctx.fillStyle = "rgba(26,31,46,0.72)";
      ctx.font = `${9 * scale}px ${font}`;
      const tw = ctx.measureText(item.advertises).width;
      ctx.fillRect(p.x - tw / 2 - 4, p.y - 36 * scale, tw + 8, 12 * scale);
      ctx.fillStyle = TOKENS.mute;
      ctx.fillText(item.advertises, p.x - tw / 2, p.y - 26 * scale);
    }
  }

  if (dim) {
    ctx.fillStyle = "rgba(8,10,16,0.48)";
    ctx.fillRect(ox, oy, FLOOR.w * scale, FLOOR.h * scale);
  }
}
