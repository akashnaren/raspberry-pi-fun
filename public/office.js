import {
  destinationFor,
  fitView,
  hudStatus,
  latestEventLine,
  poseFor,
  verbFor,
} from "./office-motion.js";

const canvas = document.getElementById("office");
const ctx = canvas.getContext("2d");
const product = document.getElementById("product");
const validator = document.getElementById("validator");
const pauseBtn = document.getElementById("pause-btn");
const pauseBadge = document.getElementById("pause-badge");
const modeBadge = document.getElementById("mode-badge");
const actingEl = document.getElementById("acting");
const budgetEl = document.getElementById("budget");
const staffEl = document.getElementById("staff-count");
const sleepBadge = document.getElementById("sleep-badge");
const soundBtn = document.getElementById("sound-btn");
const dayEl = document.getElementById("day-n");
const productTitle = document.getElementById("product-title");
const officePane = document.getElementById("office-pane");
const buildingPulse = document.getElementById("building-pulse");
const eventLine = document.getElementById("event-line");

let CELL = 30;
const DPR = 1;
const ENTER = 220;
const HOLD = 4200;
const FADE = 700;
const INDIGO = "#6366F1";

let state = {
  office: null,
  employees: [],
  events: [],
  sprites: new Map(),
  dryRun: true,
  paused: false,
  replay: false,
  sleeping: false,
  lite: false,
  budget: null,
  hud: null,
  acting: null,
  relationships: null,
  startedAt: Date.now(),
};
let view = { ox: 24, oy: 20, cell: 30, locked: true };
let bubble = null;
let hoverId = null;
let lastDraw = 0;
let lastStep = 0;
let replayIndex = 0;
let lastReplay = 0;
let lastEventText = "";

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "hello" || msg.type === "event" || msg.type === "replay") {
      applyState(msg.state, msg.event);
    }
    if (msg.type === "validate") runValidation(socket, msg);
  });
  socket.addEventListener("close", () => setTimeout(connect, 1500));
}

function applyState(next, event) {
  if (!next) return;
  const sprites = state.sprites;
  const startedAt = state.startedAt;
  state = { ...state, ...next, sprites, startedAt };
  const hud = next.hud || {};
  const used = next.budget?.spentUsd ?? hud.burnUsd ?? 0;
  const cap = next.budget?.ceilingUsd || hud.ceilingUsd || 5;
  const staff = hud.staff || (next.employees || []).length || 4;
  const sleeping = Boolean(next.sleeping || next.budget?.exhausted || hud.sleeping);
  const status = hudStatus({
    dayN: hud.dayN || 1,
    staff,
    spentUsd: used,
    ceilingUsd: cap,
    actingName: next.acting?.name,
    sleeping,
    paused: next.paused,
    replay: next.replay,
  });
  dayEl.textContent = status.day;
  staffEl.textContent = status.people;
  budgetEl.textContent = status.burn;
  actingEl.textContent = status.who;
  const productName = next.studio?.product || "Meridian Office";
  productTitle.textContent = productName;
  const mode = next.mode || (next.dryRun ? "dry-run" : "live");
  modeBadge.textContent = mode;
  document.body.classList.toggle("sleeping", sleeping);
  document.body.classList.toggle("lite", Boolean(next.lite) || new URLSearchParams(location.search).has("lite"));
  pauseBadge.classList.toggle("hidden", !next.paused || sleeping);
  sleepBadge.classList.toggle("hidden", !sleeping);
  pauseBtn.textContent = next.paused ? "resume" : "pause";
  paintEventLine(next.events || []);
  if (event) react(event);
  if (event?.type === "build_passed") product.src = `/dist/index.html?t=${event.ts}`;
  if (event?.type === "file_written" && event.data?.product) showBuilding(true);
  if (event?.type === "build_passed" || event?.type === "build_failed" || event?.type === "turn_finished") {
    showBuilding(false);
  }
  if (event?.type === "build_failed" || event?.type === "turn_failed") failFlash();
  syncSprites();
  refit();
}

function paintEventLine(events) {
  const text = latestEventLine(events);
  if (text === lastEventText) return;
  lastEventText = text;
  eventLine.classList.add("is-swap");
  window.setTimeout(() => {
    eventLine.textContent = text;
    eventLine.classList.remove("is-swap");
  }, 140);
}

function showBuilding(on) {
  buildingPulse.classList.toggle("hidden", !on);
}

function failFlash() {
  officePane.classList.remove("fail-flash");
  void officePane.offsetWidth;
  officePane.classList.add("fail-flash");
  setTimeout(() => officePane.classList.remove("fail-flash"), 600);
}

function deskFor(id) {
  return (state.office?.desks || []).find((desk) => desk.owner === id);
}

function standAtDesk(desk) {
  if (!desk) return { x: 4, y: 6 };
  return { x: desk.x + 1, y: desk.y + 2 };
}

function decor(kind) {
  return (state.office?.decor || []).find((item) => item.kind === kind);
}

function syncSprites() {
  for (const employee of state.employees || []) {
    const stand = standAtDesk(deskFor(employee.id));
    if (!state.sprites.has(employee.id)) {
      state.sprites.set(employee.id, {
        x: stand.x,
        y: stand.y,
        path: [],
        pose: "idle",
        facing: 1,
        frame: 0,
        at: "desk",
        active: 0,
        blinkUntil: 0,
      });
    }
  }
}

function react(event) {
  const sprite = state.sprites.get(event.actor);
  const target = destinationFor(event, state.office);
  if (sprite && target) {
    const path = findPath(state.office, { x: Math.round(sprite.x), y: Math.round(sprite.y) }, target);
    sprite.path = path;
    sprite.at = target.at;
    sprite.pose = path.length ? "walk" : poseFor(event);
    sprite.wantPose = poseFor(event);
    sprite.active = 1;
  }
  for (const [id, other] of state.sprites) {
    if (id !== event.actor) other.active = Math.max(0, other.active - 0.4);
  }
  if (event.type === "say" && event.data?.text) {
    const who = (state.employees || []).find((person) => person.id === event.actor);
    bubble = {
      actor: event.actor,
      lines: wrapTwo(event.data.text),
      born: Date.now(),
      color: who?.accent || who?.color || "#6e6e73",
    };
  }
}

function wrapTwo(text, width = 34) {
  const words = String(text).split(/\s+/);
  const lines = [""];
  for (const word of words) {
    const next = lines[lines.length - 1] ? `${lines[lines.length - 1]} ${word}` : word;
    if (next.length > width) {
      if (lines.length === 2) {
        lines[1] = `${lines[1].slice(0, width - 1)}…`;
        break;
      }
      lines.push(word);
    } else {
      lines[lines.length - 1] = next;
    }
  }
  return lines.slice(0, 2);
}

function blockedSet(office) {
  const blocked = new Set();
  for (const desk of office.desks || []) {
    blocked.add(`${desk.x},${desk.y}`);
    blocked.add(`${desk.x + 1},${desk.y}`);
  }
  for (const item of office.decor || []) {
    if (item.kind === "rug") continue;
    const w = item.w || (item.kind === "whiteboard" ? 4 : 1);
    const h = item.h || 1;
    for (let x = item.x; x < item.x + w; x += 1) {
      for (let y = item.y; y < item.y + h; y += 1) {
        blocked.add(`${x},${y}`);
      }
    }
  }
  return blocked;
}

function inRooms(office, x, y) {
  return (office.rooms || []).some(
    (room) => x >= room.x && y >= room.y && x < room.x + room.w && y < room.y + room.h,
  );
}

function findPath(office, start, goal) {
  if (!office) return [goal];
  const blocked = blockedSet(office);
  const startKey = `${start.x},${start.y}`;
  const goalKey = `${goal.x},${goal.y}`;
  blocked.delete(goalKey);
  blocked.delete(startKey);
  const open = [{ x: start.x, y: start.y, g: 0, f: 0, from: null }];
  const seen = new Set([startKey]);
  const dirs = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ];
  while (open.length) {
    open.sort((a, b) => a.f - b.f);
    const cur = open.shift();
    if (cur.x === goal.x && cur.y === goal.y) {
      const path = [];
      let node = cur;
      while (node) {
        path.push({ x: node.x, y: node.y });
        node = node.from;
      }
      return path.reverse().slice(1);
    }
    for (const [dx, dy] of dirs) {
      const nx = cur.x + dx;
      const ny = cur.y + dy;
      const key = `${nx},${ny}`;
      if (seen.has(key)) continue;
      if (!inRooms(office, nx, ny) || blocked.has(key)) continue;
      seen.add(key);
      const h = Math.abs(nx - goal.x) + Math.abs(ny - goal.y);
      open.push({ x: nx, y: ny, g: cur.g + 1, f: cur.g + 1 + h, from: cur });
    }
  }
  return [goal];
}

function runValidation(socket, msg) {
  let settled = false;
  const finish = (ok, error) => {
    if (settled) return;
    settled = true;
    validator.onload = null;
    validator.onerror = null;
    socket.send(JSON.stringify({ type: "validation_result", id: msg.id, ok, error }));
  };
  const timer = setTimeout(() => finish(false, "timeout"), 7000);
  validator.onload = () => {
    try {
      const doc = validator.contentDocument;
      if (!doc || !doc.documentElement) return finish(false, "empty document");
      if (doc.querySelector("parsererror")) return finish(false, "parsererror");
      finish(true, "");
    } catch (error) {
      finish(false, error.message);
    } finally {
      clearTimeout(timer);
    }
  };
  validator.onerror = () => {
    clearTimeout(timer);
    finish(false, "load error");
  };
  validator.src = msg.url;
}

function resize() {
  const pane = canvas.parentElement;
  canvas.width = Math.max(320, Math.floor(pane.clientWidth * DPR));
  canvas.height = Math.max(240, Math.floor(pane.clientHeight * DPR));
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  refit();
}

function refit() {
  view = fitView(state.office, canvas.width / DPR, canvas.height / DPR);
  CELL = view.cell;
}

function stepSprites(now) {
  const dt = Math.min(0.05, (now - (lastStep || now)) / 1000);
  lastStep = now;
  const speed = 4.4;
  let walking = false;
  for (const sprite of state.sprites.values()) {
    if (sprite.path?.length) {
      walking = true;
      const next = sprite.path[0];
      const dx = next.x - sprite.x;
      const dy = next.y - sprite.y;
      const dist = Math.hypot(dx, dy);
      if (dist < 0.06) {
        sprite.x = next.x;
        sprite.y = next.y;
        sprite.path.shift();
        if (!sprite.path.length) sprite.pose = sprite.wantPose || "idle";
      } else {
        const step = Math.min(dist, speed * dt);
        sprite.x += (dx / dist) * step;
        sprite.y += (dy / dist) * step;
        sprite.facing = dx < 0 ? -1 : 1;
        sprite.pose = "walk";
        sprite.frame = Math.floor(now / 140) % 2;
      }
    } else if (sprite.pose === "type") {
      sprite.frame = Math.floor(now / 220) % 2;
    } else {
      sprite.frame = Math.floor(now / 700) % 2;
    }
    if (sprite.pose === "idle" && now > sprite.blinkUntil + 2400 + ((sprite.x * 400) % 1800)) {
      sprite.blinkUntil = now + 120;
    }
    sprite.active *= 0.992;
  }
  return walking;
}

function roundRect(x, y, w, h, r) {
  const rad = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rad, y);
  ctx.arcTo(x + w, y, x + w, y + h, rad);
  ctx.arcTo(x + w, y + h, x, y + h, rad);
  ctx.arcTo(x, y + h, x, y, rad);
  ctx.arcTo(x, y, x + w, y, rad);
  ctx.closePath();
}

function drawOffice(ox, oy) {
  const office = state.office;
  if (!office) return;
  const dim = state.paused || state.sleeping || state.budget?.exhausted;
  const bounds = office.rooms?.reduce(
    (acc, room) => ({ w: Math.max(acc.w, room.x + room.w), h: Math.max(acc.h, room.y + room.h) }),
    { w: 22, h: 16 },
  ) || { w: 22, h: 16 };
  ctx.fillStyle = office.walls || "#c8c4bc";
  ctx.fillRect(ox - 10, oy - 10, bounds.w * CELL + 20, bounds.h * CELL + 20);
  for (const room of office.rooms || []) {
    const rx = ox + room.x * CELL;
    const ry = oy + room.y * CELL;
    const rw = room.w * CELL;
    const rh = room.h * CELL;
    const base = /break/i.test(room.name) ? "#d8d2c8" : /meeting|lab/i.test(room.name) ? "#d5d6d8" : "#e4e0d8";
    ctx.fillStyle = base;
    ctx.fillRect(rx, ry, rw, rh);
    for (let y = 0; y < room.h; y += 1) {
      for (let x = 0; x < room.w; x += 1) {
        const plank = (x + y) % 2 === 0;
        ctx.fillStyle = plank ? "rgba(90,80,70,0.06)" : "rgba(255,255,255,0.18)";
        ctx.fillRect(rx + x * CELL, ry + y * CELL, CELL, CELL);
      }
    }
    ctx.strokeStyle = "#b7b1a6";
    ctx.lineWidth = 2;
    ctx.strokeRect(rx + 1, ry + 1, rw - 2, rh - 2);
    ctx.lineWidth = 1;
    ctx.fillStyle = "#6e6e73";
    ctx.font = `${Math.max(9, Math.round(CELL * 0.36))}px ${getComputedStyle(document.body).fontFamily}`;
    ctx.fillText(room.name, rx + 8, ry + 15);
  }

  for (const lamp of office.decor || []) {
    if (lamp.kind !== "lamp") continue;
    const lx = ox + lamp.x * CELL + 10;
    const ly = oy + lamp.y * CELL + 8;
    const glow = ctx.createRadialGradient(lx, ly, 2, lx, ly, 54);
    glow.addColorStop(0, dim ? "rgba(255,244,214,0.10)" : "rgba(255,244,214,0.22)");
    glow.addColorStop(1, "rgba(255,244,214,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(lx - 54, ly - 36, 108, 90);
  }

  for (const item of office.decor || []) drawDecor(ox, oy, item);
  for (const desk of office.desks || []) drawDesk(ox, oy, desk);

  if (dim) {
    ctx.fillStyle = "rgba(245,245,247,0.28)";
    ctx.fillRect(ox - 10, oy - 10, bounds.w * CELL + 20, bounds.h * CELL + 20);
  }
}

function drawDesk(ox, oy, desk) {
  const x = ox + desk.x * CELL;
  const y = oy + desk.y * CELL;
  ctx.fillStyle = "rgba(0,0,0,0.08)";
  ctx.beginPath();
  ctx.ellipse(x + CELL * 1.05, y + CELL * 1.45, CELL * 1.1, 6, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#8a7b68";
  roundRect(x, y, CELL * 2.15, CELL * 1.28, 5);
  ctx.fill();
  ctx.fillStyle = "#9a8b76";
  ctx.fillRect(x + 4, y + 3, CELL * 2.15 - 8, 3);
  const items = desk.items || [];
  if (items.includes("monitor") || items.includes("second_monitor")) {
    ctx.fillStyle = "#2c2c2e";
    ctx.fillRect(x + 8, y + 4, 22, 14);
    ctx.fillStyle = "#d2d2d7";
    ctx.fillRect(x + 10, y + 6, 18, 10);
    if (items.includes("second_monitor")) {
      ctx.fillStyle = "#2c2c2e";
      ctx.fillRect(x + 32, y + 6, 16, 12);
      ctx.fillStyle = "#e5e5ea";
      ctx.fillRect(x + 34, y + 8, 12, 8);
    }
  }
  if (items.includes("plant")) {
    ctx.fillStyle = "#3d6b46";
    ctx.beginPath();
    ctx.arc(x + CELL * 1.75, y + 10, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(x + CELL * 1.75 - 2, y + 14, 4, 5);
  }
  if (items.includes("coffee_mug")) {
    ctx.fillStyle = "#6e6e73";
    ctx.fillRect(x + 6, y + 18, 7, 6);
  }
  if (items.includes("notebook") || items.includes("sticky_notes")) {
    ctx.fillStyle = items.includes("sticky_notes") ? "#e6d36a" : "#f2efe8";
    ctx.fillRect(x + 40, y + 18, 10, 8);
  }
  const owner = (state.employees || []).find((person) => person.id === desk.owner);
  const style = owner?.aesthetics?.desk_style || owner?.desk_style || "";
  if (/messy|cable|sticker/i.test(style)) {
    ctx.strokeStyle = "#8e8e93";
    ctx.beginPath();
    ctx.moveTo(x + 8, y + 20);
    ctx.lineTo(x + 28, y + 24);
    ctx.stroke();
    ctx.fillStyle = "#8e8e93";
    ctx.fillRect(x + 30, y + 20, 5, 5);
  }
  const tag = (owner?.name || desk.owner).split(" ")[0];
  ctx.font = "9px sans-serif";
  const tw = ctx.measureText(tag).width;
  ctx.fillStyle = "#1d1d1f";
  roundRect(x + 4, y - 12, tw + 10, 11, 2);
  ctx.fill();
  ctx.fillStyle = "#f5f5f7";
  ctx.fillText(tag, x + 10, y - 4);
}

function drawDecor(ox, oy, item) {
  const x = ox + item.x * CELL;
  const y = oy + item.y * CELL;
  if (item.kind === "whiteboard") {
    const w = (item.w || 6) * CELL;
    ctx.fillStyle = "#f7f6f2";
    ctx.fillRect(x, y, w, CELL * 1.05);
    ctx.strokeStyle = "#c7c4bb";
    ctx.strokeRect(x, y, w, CELL * 1.05);
    ctx.fillStyle = "#1d1d1f";
    ctx.font = "10px sans-serif";
    const words = String(item.text || "SHIP").split(" ");
    let line = "";
    let row = 0;
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (ctx.measureText(next).width > w - 12) {
        ctx.fillText(line, x + 6, y + 14 + row * 12);
        line = word;
        row += 1;
        if (row > 1) break;
      } else {
        line = next;
      }
    }
    if (line && row < 2) ctx.fillText(line, x + 6, y + 14 + row * 12);
  } else if (item.kind === "coffee") {
    ctx.fillStyle = "#3a3a3c";
    ctx.fillRect(x, y, CELL * 1.1, CELL * 1.1);
    ctx.fillStyle = "#d2d2d7";
    ctx.fillRect(x + 6, y + 4, 16, 8);
  } else if (item.kind === "couch") {
    ctx.fillStyle = "#6b6258";
    ctx.fillRect(x, y, CELL * 2.4, CELL * 1.1);
    ctx.fillStyle = "#4e4840";
    ctx.fillRect(x, y, 8, CELL * 1.1);
    ctx.fillRect(x + CELL * 2.1, y, 8, CELL * 1.1);
  } else if (item.kind === "plant") {
    ctx.fillStyle = "#3d6b46";
    ctx.beginPath();
    ctx.arc(x + 10, y + 8, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(x + 7, y + 14, 6, 7);
  } else if (item.kind === "lamp") {
    ctx.fillStyle = "#e6d39a";
    ctx.beginPath();
    ctx.arc(x + 8, y + 6, 4, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "rug") {
    ctx.fillStyle = "#c9c2b6";
    ctx.fillRect(x, y, CELL * 5, CELL * 3);
  } else if (item.kind === "shelf") {
    ctx.fillStyle = "#8a7b68";
    ctx.fillRect(x, y, CELL * 2, 8);
  } else if (item.kind === "clock") {
    ctx.fillStyle = "#f7f6f2";
    ctx.beginPath();
    ctx.arc(x + 8, y + 8, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#1d1d1f";
    ctx.stroke();
  } else if (item.kind === "filing_cabinet") {
    ctx.fillStyle = "#8e8e93";
    ctx.fillRect(x, y, CELL * 1.1, CELL * 1.4);
    ctx.fillStyle = "#636366";
    ctx.fillRect(x + 4, y + 6, CELL * 0.8, 6);
    ctx.fillRect(x + 4, y + 16, CELL * 0.8, 6);
  } else if (item.kind === "standing_desk") {
    ctx.fillStyle = "#8a7b68";
    ctx.fillRect(x, y, CELL * 2, 8);
    ctx.fillRect(x + 2, y + 8, 4, 16);
    ctx.fillRect(x + CELL * 1.7, y + 8, 4, 16);
  } else if (item.kind === "beanbag") {
    ctx.fillStyle = "#636366";
    ctx.beginPath();
    ctx.ellipse(x + 14, y + 12, 16, 10, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "minifridge") {
    ctx.fillStyle = "#e5e5ea";
    ctx.fillRect(x, y, 16, 22);
    ctx.fillStyle = "#8e8e93";
    ctx.fillRect(x + 12, y + 8, 2, 6);
  } else if (item.kind === "table") {
    ctx.fillStyle = "#8a7b68";
    roundRect(x, y, CELL * 3.2, CELL * 1.6, 5);
    ctx.fill();
  }
  const ad = item.advertises;
  if (ad) {
    ctx.fillStyle = "rgba(29,29,31,0.72)";
    ctx.font = "9px sans-serif";
    const label = ad;
    const tw = ctx.measureText(label).width;
    ctx.fillRect(x, y - 12, tw + 8, 11);
    ctx.fillStyle = "#f5f5f7";
    ctx.fillText(label, x + 4, y - 3);
  }
}

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
const BOTTOM_COLOR = { jeans: "#314e73", chinos: "#8a6a3d", skirt: "#5b3a78", trousers: "#2c241c", shorts: "#3f4f6b" };
const SHOE_COLOR = { sneakers: "#efe6d4", boots: "#3b2418", loafers: "#5a3b22", sandals: "#c4a574" };

function lookOf(employee) {
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

function drawPerson(ox, oy, employee, sprite, now) {
  const px = ox + sprite.x * CELL;
  const py = oy + sprite.y * CELL;
  const bob = sprite.pose === "idle" ? Math.sin(now / 400 + sprite.x) * 1.2 : 0;
  const color = employee.accent || employee.color || "#1d1d1f";
  const look = lookOf(employee);
  if (sprite.active > 0.08) {
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.22 + sprite.active * 0.35;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(px + 8, py + 4 + bob, 16, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;
  }
  ctx.fillStyle = "rgba(0,0,0,0.12)";
  ctx.beginPath();
  ctx.ellipse(px + 8, py + 22, 8, 3, 0, 0, Math.PI * 2);
  ctx.fill();
  const walk = sprite.pose === "walk" ? sprite.frame : 0;
  const type = sprite.pose === "type" ? sprite.frame : 0;
  const blink = now < (sprite.blinkUntil || 0);
  ctx.save();
  ctx.translate(px + 8, py + 4 + bob);
  ctx.scale(sprite.facing || 1, 1);
  ctx.fillStyle = look.skin;
  ctx.fillRect(-5, -2, 10, 10);
  ctx.fillStyle = look.hair;
  if (look.hairStyle === "bun") {
    ctx.fillRect(-6, -14, 12, 5);
    ctx.beginPath();
    ctx.arc(0, -16, 3, 0, Math.PI * 2);
    ctx.fill();
  } else if (look.hairStyle === "ponytail_dark") {
    ctx.fillRect(-6, -14, 12, 5);
    ctx.fillRect(5, -12, 4, 10);
  } else if (look.hairStyle === "shoulder_brown" || look.hairStyle === "long_wave") {
    ctx.fillRect(-7, -14, 14, 6);
    ctx.fillRect(-8, -10, 3, 8);
    ctx.fillRect(5, -10, 3, 8);
  } else if (look.hairStyle === "short_black_wavy") {
    ctx.fillRect(-7, -14, 14, 6);
    ctx.fillRect(-8, -10, 3, 4);
  } else if (look.hairStyle === "cropped_silver" || look.hairStyle === "buzz" || look.hairStyle === "fade") {
    ctx.fillRect(-6, -13, 12, 4);
  } else {
    ctx.fillRect(-6, -14, 12, 6);
  }
  ctx.fillStyle = look.bottom;
  ctx.fillRect(-5, 8, 4, 7 + (walk ? 1 : 0));
  ctx.fillRect(1, 8, 4, 7 + (walk ? 0 : 1));
  ctx.fillStyle = look.top;
  ctx.fillRect(-6, -2, 12, 10);
  if (look.topKind === "hoodie") {
    ctx.fillRect(-7, -4, 3, 6);
    ctx.fillRect(4, -4, 3, 6);
  }
  if (look.topKind === "blazer") ctx.fillRect(-7, -2, 2, 10);
  if (look.topKind === "cardigan") {
    ctx.fillStyle = "#312e81";
    ctx.fillRect(-2, -2, 1, 10);
  }
  if (look.topKind === "turtleneck") ctx.fillRect(-4, -4, 8, 3);
  if (look.topKind === "flannel") {
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(-5, 1, 10, 1);
  }
  ctx.fillStyle = look.shoes;
  ctx.fillRect(-5, 14, 4, 3);
  ctx.fillRect(1, 14, 4, 3);
  ctx.fillStyle = look.skin;
  ctx.fillRect(-5, -10, 10, 8);
  if (blink) {
    ctx.fillStyle = look.skin;
    ctx.fillRect(-3, -7, 2, 1);
    ctx.fillRect(1, -7, 2, 1);
  } else {
    ctx.fillStyle = "#1b1b1b";
    ctx.fillRect(-3, -7, 2, 2);
    ctx.fillRect(1, -7, 2, 2);
  }
  const arm = sprite.pose === "type" ? -4 - type * 2 : sprite.pose === "walk" ? walk * 3 - 1 : 1;
  ctx.fillStyle = look.skin;
  ctx.fillRect(-8, 0, 3, 7 + (sprite.pose === "talk" ? 1 : 0));
  ctx.fillRect(5, arm, 3, 7);
  if (look.accessory === "glasses") {
    ctx.strokeStyle = "#1b1b1b";
    ctx.strokeRect(-4, -8, 3, 2);
    ctx.strokeRect(1, -8, 3, 2);
  }
  if (look.accessory === "earbuds") {
    ctx.fillStyle = "#eee";
    ctx.fillRect(-6, -6, 2, 2);
  }
  if (look.accessory === "badge") {
    ctx.fillStyle = INDIGO;
    ctx.fillRect(3, 2, 3, 4);
  }
  if (look.accessory === "watch") {
    ctx.fillStyle = "#d4af37";
    ctx.fillRect(-8, 4, 3, 2);
  }
  if (look.accessory === "keys") {
    ctx.fillStyle = "#d4af37";
    ctx.beginPath();
    ctx.arc(6, 10, 2, 0, Math.PI * 2);
    ctx.fill();
  }
  if (look.accessory === "scarf") {
    ctx.fillStyle = INDIGO;
    ctx.fillRect(-6, 0, 12, 2);
  }
  ctx.restore();

  const first = employee.name.split(" ")[0];
  const verb = verbFor(sprite.pose, sprite.at);
  ctx.font = "10px sans-serif";
  const plate = `${first}  ${employee.role}`;
  const tw = ctx.measureText(plate).width;
  ctx.fillStyle = "#1d1d1f";
  ctx.fillRect(px + 8 - tw / 2 - 4, py + 28, tw + 8, 12);
  ctx.fillStyle = "#f5f5f7";
  ctx.fillText(plate, px + 8 - tw / 2 + 2, py + 37);
  ctx.font = "9px sans-serif";
  const vw = ctx.measureText(verb).width;
  ctx.fillStyle = "rgba(29,29,31,0.78)";
  ctx.fillRect(px + 8 - vw / 2 - 3, py - 18, vw + 6, 11);
  ctx.fillStyle = "#f5f5f7";
  ctx.fillText(verb, px + 8 - vw / 2, py - 9);

  if (hoverId === employee.id) {
    const chip = `${employee.name.split(" ")[0]} · ${employee.model || employee.modelFamily}`;
    ctx.font = "10px sans-serif";
    const cw = ctx.measureText(chip).width;
    ctx.fillStyle = "#1d1d1f";
    ctx.fillRect(px - 4, py - 28, cw + 10, 14);
    ctx.fillStyle = "#f5f5f7";
    ctx.fillText(chip, px, py - 18);
    const rel = feelLine(employee.id);
    if (rel) {
      ctx.fillStyle = "#6e6e73";
      ctx.fillText(rel, px - 4, py - 32);
    }
  }
}

function feelLine(id) {
  const pairs = state.relationships?.pairs || [];
  const hit = pairs.find((pair) => pair.a === id || pair.b === id);
  if (!hit) return "";
  const other = hit.a === id ? hit.b : hit.a;
  const who = (state.employees || []).find((person) => person.id === other);
  const sign = hit.score > 0 ? "+" : "";
  return `feels about ${who?.name.split(" ")[0] || other}: ${sign}${hit.score} ${hit.note || ""}`;
}

function drawBubble(ox, oy, now) {
  if (!bubble) return false;
  const age = now - bubble.born;
  const life = ENTER + HOLD + FADE;
  if (age > life) {
    bubble = null;
    return false;
  }
  let alpha = 1;
  let scale = 1;
  if (age < ENTER) {
    scale = 0.85 + 0.15 * (age / ENTER);
    alpha = age / ENTER;
  } else if (age > ENTER + HOLD) {
    alpha = 1 - (age - ENTER - HOLD) / FADE;
  }
  const sprite = state.sprites.get(bubble.actor);
  if (!sprite) return true;
  const x = ox + sprite.x * CELL + 18;
  const y = oy + sprite.y * CELL - 36;
  ctx.save();
  ctx.globalAlpha = Math.max(0, alpha);
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  const lines = bubble.lines;
  ctx.font = "11px sans-serif";
  const w = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 16;
  const h = 12 + lines.length * 12;
  ctx.fillStyle = "#ffffff";
  roundRect(0, 0, w, h, 5);
  ctx.fill();
  ctx.strokeStyle = "#d2d2d7";
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(8, h);
  ctx.lineTo(4, h + 7);
  ctx.lineTo(16, h);
  ctx.fill();
  ctx.fillStyle = "#1d1d1f";
  lines.forEach((line, i) => ctx.fillText(line, 8, 14 + i * 12));
  ctx.restore();
  return true;
}

function maybeReplay(now) {
  if (!(state.paused || state.sleeping || state.replay || state.budget?.exhausted)) return;
  if (now - lastReplay < 6000) return;
  const replayable = (state.events || []).filter((event) => event.actor && event.actor !== "system");
  if (!replayable.length) return;
  lastReplay = now;
  const event = replayable[replayIndex % replayable.length];
  replayIndex += 1;
  react(event);
}

function draw(now) {
  const w = canvas.width / DPR;
  const h = canvas.height / DPR;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#ececee";
  ctx.fillRect(0, 0, w, h);
  const ox = view.ox;
  const oy = view.oy;
  drawOffice(ox, oy);
  const walking = stepSprites(now);
  for (const employee of state.employees || []) {
    const sprite = state.sprites.get(employee.id);
    if (sprite) drawPerson(ox, oy, employee, sprite, now);
  }
  const talking = drawBubble(ox, oy, now);
  maybeReplay(now);
  return walking || talking;
}

function loop(ts) {
  const walkingTalking = stepNeed(ts);
  const interval = walkingTalking ? 33 : 120;
  if (ts - lastDraw >= interval) {
    draw(ts);
    lastDraw = ts;
  }
  requestAnimationFrame(loop);
}

function stepNeed(ts) {
  if (hoverId) return true;
  if (bubble && ts - bubble.born < ENTER + HOLD + FADE) return true;
  for (const sprite of state.sprites.values()) {
    if (sprite.path?.length || sprite.active > 0.05) return true;
  }
  if ((state.paused || state.sleeping || state.replay || state.budget?.exhausted) && ts - lastReplay > 5500) {
    return true;
  }
  return false;
}

canvas.addEventListener("mousemove", (event) => {
  const rect = canvas.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * (canvas.width / DPR);
  const y = ((event.clientY - rect.top) / rect.height) * (canvas.height / DPR);
  hoverId = null;
  const ox = view.ox;
  const oy = view.oy;
  for (const employee of state.employees || []) {
    const sprite = state.sprites.get(employee.id);
    if (!sprite) continue;
    const px = ox + sprite.x * CELL;
    const py = oy + sprite.y * CELL;
    if (Math.hypot(x - px - 8, y - py) < 22) hoverId = employee.id;
  }
});
canvas.addEventListener("mouseleave", () => {
  hoverId = null;
});

canvas.addEventListener("click", () => {
  const inspect = document.getElementById("inspect");
  const title = document.getElementById("inspect-title");
  const body = document.getElementById("inspect-body");
  if (hoverId) {
    const person = (state.employees || []).find((item) => item.id === hoverId);
    const modelEl = document.getElementById("inspect-model");
    title.textContent = person?.name || hoverId;
    if (modelEl) modelEl.textContent = person?.model || person?.modelFamily || "";
    const feel = feelLine(person?.id || hoverId);
    body.textContent = [person?.role, person?.priorities, feel].filter(Boolean).join(" · ");
    inspect.classList.remove("hidden");
    return;
  }
  const board = decor("whiteboard");
  if (board) {
    const modelEl = document.getElementById("inspect-model");
    title.textContent = "whiteboard";
    if (modelEl) modelEl.textContent = "";
    body.textContent = board.text || "SHIP";
    inspect.classList.remove("hidden");
    return;
  }
  inspect.classList.add("hidden");
});

pauseBtn.addEventListener("click", async () => {
  const path = state.paused ? "/api/resume" : "/api/pause";
  await fetch(path, { method: "POST" });
});

let audio = null;
function toggleSound() {
  const on = soundBtn.getAttribute("aria-pressed") === "true";
  if (on) {
    audio?.stop();
    audio = null;
    soundBtn.setAttribute("aria-pressed", "false");
    soundBtn.textContent = "sound off";
    return;
  }
  const ctxAudio = new (window.AudioContext || window.webkitAudioContext)();
  const osc = ctxAudio.createOscillator();
  const gain = ctxAudio.createGain();
  const filter = ctxAudio.createBiquadFilter();
  osc.type = "sine";
  osc.frequency.value = 110;
  filter.type = "lowpass";
  filter.frequency.value = 420;
  gain.gain.value = 0.03;
  osc.connect(filter);
  filter.connect(gain);
  gain.connect(ctxAudio.destination);
  osc.start();
  audio = {
    stop() {
      osc.stop();
      ctxAudio.close();
    },
  };
  soundBtn.setAttribute("aria-pressed", "true");
  soundBtn.textContent = "sound on";
}
soundBtn.addEventListener("click", toggleSound);

window.addEventListener("resize", resize);
resize();
syncSprites();
connect();
requestAnimationFrame(loop);
