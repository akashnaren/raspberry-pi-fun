import { deskOwner, drawNormOffice, isNorm, layout, standAtDesk as standDesk, toPx, TOKENS } from "./art.js";

const canvas = document.getElementById("office");
const ctx = canvas.getContext("2d");
const tickerEl = document.getElementById("ticker");
const product = document.getElementById("product");
const validator = document.getElementById("validator");
const pauseBtn = document.getElementById("pause-btn");
const pauseBadge = document.getElementById("pause-badge");
const modeBadge = document.getElementById("mode-badge");
const onAir = document.getElementById("on-air");
const actingEl = document.getElementById("acting");
const budgetEl = document.getElementById("budget");
const burnBar = document.getElementById("burn-bar");
const staffEl = document.getElementById("staff-count");
const sleepBadge = document.getElementById("sleep-badge");
const soundBtn = document.getElementById("sound-btn");
const dayEl = document.getElementById("day-n");
const taskEl = document.getElementById("current-task");
const shipEl = document.getElementById("ship-line");
const productTitle = document.getElementById("product-title");
const spotAvatar = document.getElementById("spot-avatar");
const spotTool = document.getElementById("spot-tool");
const officePane = document.getElementById("office-pane");
const buildingPulse = document.getElementById("building-pulse");

const CELL = 30;
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
let bubble = null;
let hoverId = null;
let lastDraw = 0;
let lastStep = 0;
let replayIndex = 0;
let lastReplay = 0;
const camera = { x: 0, y: 0, tx: 0, ty: 0 };

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
  if (next.studio) {
    document.getElementById("studio-name").textContent = next.studio.name;
    document.getElementById("product-name").textContent = next.studio.product;
  }
  const hud = next.hud || {};
  const used = next.budget?.spentUsd ?? hud.burnUsd ?? 0;
  const cap = next.budget?.ceilingUsd || hud.ceilingUsd || 5;
  budgetEl.textContent = `$${used.toFixed(2)} / $${cap.toFixed(0)}`;
  burnBar.style.width = `${Math.min(100, (used / cap) * 100)}%`;
  const staff = hud.staff || (next.employees || []).length || 4;
  staffEl.textContent = `${staff} staff`;
  const cast = document.getElementById("cast-line");
  if (cast) {
    const named = (next.employees || [])
      .filter((person) => ["nova", "kessler", "mira"].includes(person.id))
      .map((person) => person.name.split(" ")[0]);
    if (named.length) cast.textContent = named.join(" · ");
  }
  paintModelChips(next.employees || next.hud?.models || []);
  dayEl.textContent = `Day ${hud.dayN || 1}`;
  taskEl.textContent = hud.currentTask || currentTaskFrom(next) || "waiting for a task";
  shipEl.textContent = hud.shipLine || "shipping when green";
  const productName = next.studio?.product || "Timezone Buddy";
  productTitle.textContent = productName;
  document.getElementById("product-name").textContent = productName;
  const mode = next.mode || (next.dryRun ? "dry-run" : "live");
  modeBadge.textContent = mode;
  modeBadge.classList.toggle("live", mode === "live");
  const sleeping = Boolean(next.sleeping || next.budget?.exhausted || hud.sleeping);
  document.body.classList.toggle("sleeping", sleeping);
  document.body.classList.toggle("lite", Boolean(next.lite) || new URLSearchParams(location.search).has("lite"));
  const air = mode === "live" && !next.paused && !sleeping;
  onAir.classList.toggle("hidden", !air);
  officePane.classList.toggle("live", air);
  pauseBadge.classList.toggle("hidden", !next.paused || sleeping);
  sleepBadge.classList.toggle("hidden", !sleeping);
  pauseBtn.textContent = next.paused ? "Resume" : "Pause";
  paintTicker(next.events || []);
  paintActing(next, sleeping);
  paintSpotlight(next);
  if (event) react(event);
  if (event?.type === "build_passed") product.src = `/dist/index.html?t=${event.ts}`;
  if (event?.type === "file_written" && event.data?.product) showBuilding(true);
  if (event?.type === "build_passed" || event?.type === "build_failed" || event?.type === "turn_finished") {
    showBuilding(false);
  }
  if (event?.type === "build_failed" || event?.type === "turn_failed") failFlash();
  syncSprites();
}

function paintModelChips(employees) {
  const row = document.getElementById("model-chips");
  if (!row) return;
  row.replaceChildren();
  for (const person of employees) {
    const chip = document.createElement("span");
    chip.className = "model-chip";
    chip.dataset.id = person.id;
    chip.title = person.model || person.family || person.modelFamily || "";
    chip.style.borderLeftColor = person.accent || person.color || "#F59E0B";
    const name = document.createElement("b");
    name.textContent = (person.name || person.id).split(" ")[0];
    const model = document.createElement("code");
    model.textContent = person.model || person.family || person.modelFamily || "—";
    chip.append(name, model);
    row.append(chip);
  }
}

function currentTaskFrom(next) {
  const open = (next.backlog?.tasks || []).find((task) => task.status === "open");
  return open?.text || "";
}

function showBuilding(on) {
  buildingPulse.classList.toggle("hidden", !on);
}

function failFlash() {
  officePane.classList.remove("fail-flash");
  void officePane.offsetWidth;
  officePane.classList.add("fail-flash");
  setTimeout(() => officePane.classList.remove("fail-flash"), 800);
}

function paintSpotlight(next) {
  const acting = next.acting;
  const employee = (next.employees || []).find((person) => person.id === acting?.id);
  const color = employee?.accent || employee?.color || "#F59E0B";
  const initial = (acting?.name || "?").slice(0, 1);
  spotAvatar.textContent = initial;
  spotAvatar.style.background = color;
  spotTool.textContent = acting?.tool || "—";
}

function paintTicker(events) {
  const lines = events
    .filter((item) => !["model_resolved", "turn_finished"].includes(item.type))
    .map((item) => item.headline || item.message)
    .filter((line) => line && !/^\s*\{/.test(line));
  const crawl = lines.slice(-5).join("     ·     ");
  tickerEl.textContent = crawl ? `${crawl}     ·     ${crawl}` : "the office is quiet";
}

function paintActing(next, sleeping) {
  if (sleeping) {
    actingEl.textContent = "token ceiling — world paused";
    return;
  }
  if (next.replay) {
    actingEl.textContent = "replay — walking the event log, zero tokens";
    return;
  }
  if (next.paused) {
    actingEl.textContent = "paused — replaying the last hour of events";
    return;
  }
  const acting = next.acting;
  if (!acting) {
    actingEl.textContent = "waiting for someone to act";
    return;
  }
  actingEl.textContent = `${acting.name} is acting — ${placeLabel(acting.id)}`;
}

function placeLabel(id) {
  const sprite = state.sprites.get(id);
  if (!sprite?.at) return "at their desk";
  if (sprite.at === "whiteboard") return "at the whiteboard";
  if (sprite.at === "coffee") return "at the coffee machine";
  if (sprite.at === "lab" || sprite.at === "meeting") return "in the meeting room";
  if (sprite.at === "couch") return "on the break-room couch";
  return "at their desk";
}

function deskFor(id) {
  return (state.office?.desks || []).find((desk) => deskOwner(desk) === id);
}

function standAtDesk(desk) {
  return standDesk(desk, state.office);
}

function decor(kind) {
  const aliases = {
    coffee: ["coffee", "coffee_machine"],
    whiteboard: ["whiteboard"],
    couch: ["couch", "couch_2seat"],
    table: ["table", "table_round"],
  };
  const names = aliases[kind] || [kind];
  const surface = [...(state.office?.decor || []), ...(state.office?.objects || [])];
  return surface.find((item) => names.includes(item.kind) || names.includes(item.prop));
}

function currentLayout() {
  const lay = layout(canvas, DPR, state.office);
  lay.ox += camera.x;
  lay.oy += camera.y;
  return lay;
}

function targetFor(event) {
  const office = state.office;
  if (!office || event.actor === "system") return null;
  if (event.type === "request_filed") {
    const jules = deskFor("jules");
    if (jules) return { ...standAtDesk(jules), at: "desk" };
  }
  if (event.type === "office_edited" || event.type === "aesthetics_changed") {
    const desk = deskFor(event.actor);
    if (desk) return { ...standAtDesk(desk), at: "desk" };
  }
  if (event.type === "task_added" || event.type === "task_closed") {
    const board = decor("whiteboard");
    if (board) return { x: board.x, y: board.y, at: "whiteboard" };
  }
  if (event.type === "build_failed") {
    const meeting = (office.rooms || []).find((room) => /meeting|lab/i.test(room.name));
    if (meeting) return { x: meeting.x + 0.05, y: meeting.y + 0.05, at: "meeting" };
  }
  if (event.type === "say" && /coffee|break/i.test(event.data?.text || "")) {
    const coffee = decor("coffee");
    if (coffee) return { x: coffee.x, y: coffee.y, at: "coffee" };
  }
  const desk = deskFor(event.actor);
  const stand = standAtDesk(desk);
  return { ...stand, at: "desk" };
}

function poseFor(event) {
  if (event.type === "file_written" || event.type === "file_read" || event.type === "journal") return "type";
  if (event.type === "say") return "talk";
  return "idle";
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
  const target = targetFor(event);
  if (sprite && target) {
    const path = findPath(state.office, { x: sprite.x, y: sprite.y }, target);
    sprite.path = path;
    sprite.at = target.at;
    sprite.pose = path.length ? "walk" : poseFor(event);
    sprite.wantPose = poseFor(event);
    sprite.active = 1;
    const lay = layout(canvas, DPR, state.office);
    const focus = toPx(target.x, target.y, lay);
    camera.tx = canvas.width / (2 * DPR) - focus.x;
    camera.ty = canvas.height / (2 * DPR) - focus.y;
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
      color: who?.accent || who?.color || "#d7b07a",
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

function toTile(nx, ny, office) {
  if (isNorm(office)) return { x: Math.round(nx * 20), y: Math.round(ny * 15) };
  return { x: Math.round(nx), y: Math.round(ny) };
}

function fromTile(tx, ty, office) {
  if (isNorm(office)) return { x: tx / 20, y: ty / 15 };
  return { x: tx, y: ty };
}

function blockedSet(office) {
  const blocked = new Set();
  const mark = (nx, ny) => {
    const tile = toTile(nx, ny, office);
    blocked.add(`${tile.x},${tile.y}`);
  };
  for (const desk of office.desks || []) {
    mark(desk.x, desk.y);
  }
  for (const item of [...(office.decor || []), ...(office.objects || [])]) {
    if (item.kind === "rug" || item.prop === "rug_round") continue;
    mark(item.x, item.y);
  }
  return blocked;
}

function inRooms(office, tx, ty) {
  const pos = fromTile(tx, ty, office);
  const rooms = office.rooms || [];
  if (!rooms.length) return pos.x >= 0 && pos.x <= 1 && pos.y >= 0 && pos.y <= 1;
  return rooms.some(
    (room) => pos.x >= room.x && pos.y >= room.y && pos.x < room.x + room.w && pos.y < room.y + room.h,
  );
}

function findPath(office, start, goal) {
  if (!office) return [goal];
  const a = toTile(start.x, start.y, office);
  const b = toTile(goal.x, goal.y, office);
  const blocked = blockedSet(office);
  const startKey = `${a.x},${a.y}`;
  const goalKey = `${b.x},${b.y}`;
  blocked.delete(goalKey);
  blocked.delete(startKey);
  const open = [{ x: a.x, y: a.y, g: 0, f: 0, from: null }];
  const seen = new Set([startKey]);
  const dirs = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ];
  while (open.length) {
    open.sort((left, right) => left.f - right.f);
    const cur = open.shift();
    if (cur.x === b.x && cur.y === b.y) {
      const path = [];
      let node = cur;
      while (node) {
        path.push(fromTile(node.x, node.y, office));
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
      const h = Math.abs(nx - b.x) + Math.abs(ny - b.y);
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
}

function stepSprites(now) {
  const dt = Math.min(0.05, (now - (lastStep || now)) / 1000);
  lastStep = now;
  const speed = isNorm(state.office) ? 0.35 : 4.4;
  let walking = false;
  for (const sprite of state.sprites.values()) {
    if (sprite.path?.length) {
      walking = true;
      const next = sprite.path[0];
      const dx = next.x - sprite.x;
      const dy = next.y - sprite.y;
      const dist = Math.hypot(dx, dy);
      if (dist < (isNorm(state.office) ? 0.012 : 0.06)) {
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
    if (sprite.pose === "idle" && now > sprite.blinkUntil + 2400 + (sprite.x * 400) % 1800) {
      sprite.blinkUntil = now + 120;
    }
    sprite.active *= 0.992;
  }
  camera.x += (camera.tx - camera.x) * 0.06;
  camera.y += (camera.ty - camera.y) * 0.06;
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
  if (isNorm(office)) {
    const lay = currentLayout();
    drawNormOffice(ctx, office, lay, dim, getComputedStyle(document.body).fontFamily);
    return;
  }
  const bounds = office.rooms?.reduce(
    (acc, room) => ({ w: Math.max(acc.w, room.x + room.w), h: Math.max(acc.h, room.y + room.h) }),
    { w: 22, h: 16 },
  ) || { w: 22, h: 16 };
  ctx.fillStyle = office.walls || "#2a2118";
  ctx.fillRect(ox - 14, oy - 14, bounds.w * CELL + 28, bounds.h * CELL + 28);
  for (const room of office.rooms || []) {
    const rx = ox + room.x * CELL;
    const ry = oy + room.y * CELL;
    const rw = room.w * CELL;
    const rh = room.h * CELL;
    const base = /break/i.test(room.name) ? "#2a2018" : /meeting|lab/i.test(room.name) ? "#1c1b20" : "#221c16";
    ctx.fillStyle = base;
    ctx.fillRect(rx, ry, rw, rh);
    for (let y = 0; y < room.h; y += 1) {
      for (let x = 0; x < room.w; x += 1) {
        const plank = (x + y) % 2 === 0;
        ctx.fillStyle = plank ? "rgba(90,64,40,0.16)" : "rgba(0,0,0,0.08)";
        ctx.fillRect(rx + x * CELL, ry + y * CELL, CELL, CELL);
      }
    }
    ctx.strokeStyle = "#4a3b2c";
    ctx.lineWidth = 3;
    ctx.strokeRect(rx + 1.5, ry + 1.5, rw - 3, rh - 3);
    ctx.lineWidth = 1;
    ctx.fillStyle = "#c9b59a";
    ctx.font = "11px sans-serif";
    ctx.fillText(room.name, rx + 8, ry + 16);
  }

  for (const lamp of office.decor || []) {
    if (lamp.kind !== "lamp") continue;
    const lx = ox + lamp.x * CELL + 10;
    const ly = oy + lamp.y * CELL + 8;
    const glow = ctx.createRadialGradient(lx, ly, 2, lx, ly, 90);
    glow.addColorStop(0, dim ? "rgba(245,158,11,0.12)" : "rgba(245,158,11,0.38)");
    glow.addColorStop(1, "rgba(245,158,11,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(lx - 90, ly - 60, 180, 150);
  }
  for (const desk of office.desks || []) {
    const lamp = { x: ox + (desk.x + 1) * CELL, y: oy + desk.y * CELL };
    const glow = ctx.createRadialGradient(lamp.x, lamp.y, 4, lamp.x, lamp.y, 86);
    glow.addColorStop(0, dim ? "rgba(245,158,11,0.10)" : "rgba(245,158,11,0.30)");
    glow.addColorStop(1, "rgba(245,158,11,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(lamp.x - 86, lamp.y - 48, 172, 140);
  }

  for (const item of office.decor || []) drawDecor(ox, oy, item);
  for (const desk of office.desks || []) drawDesk(ox, oy, desk);

  if (dim) {
    ctx.fillStyle = "rgba(8,6,4,0.48)";
    ctx.fillRect(ox - 14, oy - 14, bounds.w * CELL + 28, bounds.h * CELL + 28);
  }
}

function drawDesk(ox, oy, desk) {
  const x = ox + desk.x * CELL;
  const y = oy + desk.y * CELL;
  ctx.fillStyle = "rgba(0,0,0,0.32)";
  ctx.beginPath();
  ctx.ellipse(x + CELL * 1.05, y + CELL * 1.45, CELL * 1.1, 7, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#3a2a20";
  roundRect(x + 10, y + CELL * 1.05, 16, 14, 4);
  ctx.fill();
  ctx.fillStyle = "#5c4332";
  roundRect(x, y, CELL * 2.15, CELL * 1.28, 7);
  ctx.fill();
  ctx.fillStyle = "#6d5240";
  ctx.fillRect(x + 4, y + 3, CELL * 2.15 - 8, 4);
  ctx.fillStyle = "#3d2b20";
  ctx.fillRect(x + 3, y + CELL * 1.18, 8, 8);
  ctx.fillRect(x + CELL * 1.75, y + CELL * 1.18, 8, 8);
  const items = desk.items || [];
  if (items.includes("monitor") || items.includes("second_monitor")) {
    ctx.fillStyle = "#1a1f24";
    ctx.fillRect(x + 8, y + 4, 22, 14);
    ctx.fillStyle = "#7ec8c0";
    ctx.fillRect(x + 10, y + 6, 18, 10);
    if (items.includes("second_monitor")) {
      ctx.fillStyle = "#1a1f24";
      ctx.fillRect(x + 32, y + 6, 16, 12);
      ctx.fillStyle = "#d7b07a";
      ctx.fillRect(x + 34, y + 8, 12, 8);
    }
  }
  if (items.includes("plant")) {
    ctx.fillStyle = "#2f6b3a";
    ctx.beginPath();
    ctx.arc(x + CELL * 1.75, y + 10, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(x + CELL * 1.75 - 2, y + 14, 4, 5);
  }
  if (items.includes("coffee_mug")) {
    ctx.fillStyle = "#c45c1a";
    ctx.fillRect(x + 6, y + 18, 7, 6);
  }
  if (items.includes("notebook") || items.includes("sticky_notes")) {
    ctx.fillStyle = items.includes("sticky_notes") ? "#e6d36a" : "#efe6d4";
    ctx.fillRect(x + 40, y + 18, 10, 8);
  }
  const owner = (state.employees || []).find((person) => person.id === desk.owner);
  const style = owner?.aesthetics?.desk_style || owner?.desk_style || "";
  if (/messy|cable|sticker/i.test(style)) {
    ctx.strokeStyle = "#2a2a2a";
    ctx.beginPath();
    ctx.moveTo(x + 8, y + 20);
    ctx.lineTo(x + 28, y + 24);
    ctx.stroke();
    ctx.fillStyle = "#F97316";
    ctx.fillRect(x + 30, y + 20, 5, 5);
  }
  const tag = (owner?.name || desk.owner).split(" ")[0];
  ctx.font = "9px sans-serif";
  const tw = ctx.measureText(tag).width;
  ctx.fillStyle = "#1b140e";
  roundRect(x + 4, y - 12, tw + 10, 11, 3);
  ctx.fill();
  ctx.fillStyle = owner?.accent || owner?.color || INDIGO;
  ctx.fillRect(x + 4, y - 12, 3, 11);
  ctx.fillStyle = "#f3eadc";
  ctx.fillText(tag, x + 10, y - 4);
}

function drawDecor(ox, oy, item) {
  const x = ox + item.x * CELL;
  const y = oy + item.y * CELL;
  if (item.kind === "whiteboard") {
    const w = (item.w || 6) * CELL;
    ctx.fillStyle = "#e8e0d2";
    ctx.fillRect(x, y, w, CELL * 1.05);
    ctx.fillStyle = "#2b2418";
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
    ctx.fillStyle = "#3b2a22";
    ctx.fillRect(x, y, CELL * 1.1, CELL * 1.1);
    ctx.fillStyle = "#d7b07a";
    ctx.fillRect(x + 6, y + 4, 16, 8);
    ctx.fillStyle = "rgba(230,220,200,0.35)";
    ctx.fillRect(x + 10, y - 6, 3, 8);
  } else if (item.kind === "couch") {
    ctx.fillStyle = "#6a3a32";
    ctx.fillRect(x, y, CELL * 2.4, CELL * 1.1);
    ctx.fillStyle = "#4a2822";
    ctx.fillRect(x, y, 8, CELL * 1.1);
    ctx.fillRect(x + CELL * 2.1, y, 8, CELL * 1.1);
  } else if (item.kind === "plant") {
    ctx.fillStyle = "#2f6b3a";
    ctx.beginPath();
    ctx.arc(x + 10, y + 8, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#6b3a22";
    ctx.fillRect(x + 7, y + 14, 6, 7);
  } else if (item.kind === "lamp") {
    ctx.fillStyle = "#f3c66b";
    ctx.beginPath();
    ctx.arc(x + 8, y + 6, 5, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "rug") {
    ctx.fillStyle = "#4a2d22";
    ctx.fillRect(x, y, CELL * 5, CELL * 3);
  } else if (item.kind === "shelf") {
    ctx.fillStyle = "#4a3428";
    ctx.fillRect(x, y, CELL * 2, 8);
  } else if (item.kind === "clock") {
    ctx.fillStyle = "#efe6d4";
    ctx.beginPath();
    ctx.arc(x + 8, y + 8, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#2b2418";
    ctx.stroke();
  } else if (item.kind === "filing_cabinet") {
    ctx.fillStyle = "#4a4a55";
    ctx.fillRect(x, y, CELL * 1.1, CELL * 1.4);
    ctx.fillStyle = "#2a2a33";
    ctx.fillRect(x + 4, y + 6, CELL * 0.8, 6);
    ctx.fillRect(x + 4, y + 16, CELL * 0.8, 6);
  } else if (item.kind === "standing_desk") {
    ctx.fillStyle = "#6a5340";
    ctx.fillRect(x, y, CELL * 2, 8);
    ctx.fillRect(x + 2, y + 8, 4, 16);
    ctx.fillRect(x + CELL * 1.7, y + 8, 4, 16);
  } else if (item.kind === "beanbag") {
    ctx.fillStyle = "#4338ca";
    ctx.beginPath();
    ctx.ellipse(x + 14, y + 12, 16, 10, 0, 0, Math.PI * 2);
    ctx.fill();
  } else if (item.kind === "minifridge") {
    ctx.fillStyle = "#dfe4ea";
    ctx.fillRect(x, y, 16, 22);
    ctx.fillStyle = "#8aa";
    ctx.fillRect(x + 12, y + 8, 2, 6);
  } else if (item.kind === "table") {
    ctx.fillStyle = "#5a4030";
    roundRect(x, y, CELL * 3.2, CELL * 1.6, 6);
    ctx.fill();
    ctx.fillStyle = "#3d2b20";
    ctx.fillRect(x + 6, y + CELL * 1.5, 6, 10);
    ctx.fillRect(x + CELL * 2.8, y + CELL * 1.5, 6, 10);
  }
  const ad = item.advertises;
  if (ad) {
    ctx.fillStyle = "rgba(27,20,14,0.72)";
    ctx.font = "9px sans-serif";
    const label = ad;
    const tw = ctx.measureText(label).width;
    ctx.fillRect(x, y - 12, tw + 8, 11);
    ctx.fillStyle = "#e8d7b8";
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
  const lay = currentLayout();
  const p = toPx(sprite.x, sprite.y, lay);
  const px = p.x;
  const py = p.y;
  const bob = sprite.pose === "idle" ? Math.sin(now / 400 + sprite.x) * 1.2 : 0;
  const color = employee.accent || employee.color || "#F97316";
  const look = lookOf(employee);
  if (sprite.active > 0.08) {
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.35 + sprite.active * 0.5;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(px + 8, py + 4 + bob, 16, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 1;
  }
  ctx.fillStyle = "rgba(0,0,0,0.28)";
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
  const at = sprite.at || "desk";
  const verb =
    sprite.pose === "walk"
      ? `walking to ${at}`
      : sprite.pose === "type"
        ? `at ${at} · typing`
        : sprite.pose === "talk"
          ? `at ${at}`
          : `at ${at}`;
  ctx.font = "600 11px ui-sans-serif, system-ui, sans-serif";
  const plate = first;
  const tw = ctx.measureText(plate).width;
  ctx.fillStyle = "rgba(26, 31, 46, 0.88)";
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.fillRect(px + 8 - tw / 2 - 8, py + 22, tw + 16, 16);
  ctx.strokeRect(px + 8 - tw / 2 - 8, py + 22, tw + 16, 16);
  ctx.fillStyle = TOKENS.text;
  ctx.fillText(plate, px + 8 - tw / 2, py + 34);
  ctx.font = "9px ui-sans-serif, system-ui, sans-serif";
  const vw = ctx.measureText(verb).width;
  ctx.fillStyle = "rgba(26, 31, 46, 0.78)";
  ctx.fillRect(px + 8 - vw / 2 - 3, py - 18, vw + 6, 11);
  ctx.fillStyle = TOKENS.mute;
  ctx.fillText(verb, px + 8 - vw / 2, py - 9);

  if (hoverId === employee.id) {
    const chip = `${employee.name.split(" ")[0]} · ${employee.model || employee.modelFamily}`;
    ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
    const cw = ctx.measureText(chip).width;
    ctx.fillStyle = "rgba(26, 31, 46, 0.92)";
    ctx.fillRect(px - 4, py - 28, cw + 10, 14);
    ctx.fillStyle = TOKENS.text;
    ctx.fillText(chip, px, py - 18);
    const rel = feelLine(employee.id);
    if (rel) {
      ctx.fillStyle = TOKENS.mute;
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
  const lay = currentLayout();
  const p = toPx(sprite.x, sprite.y, lay);
  const x = p.x + 18;
  const y = p.y - 36;
  ctx.save();
  ctx.globalAlpha = Math.max(0, alpha);
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  const lines = bubble.lines;
  ctx.font = "500 12px ui-sans-serif, system-ui, sans-serif";
  const w = Math.max(...lines.map((line) => ctx.measureText(line).width)) + 16;
  const h = 12 + lines.length * 12;
  ctx.fillStyle = "rgba(36, 42, 58, 0.94)";
  roundRect(0, 0, w, h, 10);
  ctx.fill();
  ctx.strokeStyle = bubble.color || TOKENS.mira;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(8, h);
  ctx.lineTo(4, h + 7);
  ctx.lineTo(16, h);
  ctx.fill();
  ctx.fillStyle = TOKENS.text;
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
  ctx.fillStyle = "#120e0b";
  ctx.fillRect(0, 0, w, h);
  const ox = 20 + camera.x;
  const oy = 16 + camera.y;
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
  if (Math.abs(camera.x - camera.tx) > 0.4 || Math.abs(camera.y - camera.ty) > 0.4) return true;
  return false;
}

canvas.addEventListener("mousemove", (event) => {
  const rect = canvas.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * (canvas.width / DPR);
  const y = ((event.clientY - rect.top) / rect.height) * (canvas.height / DPR);
  hoverId = null;
  const lay = currentLayout();
  for (const employee of state.employees || []) {
    const sprite = state.sprites.get(employee.id);
    if (!sprite) continue;
    const p = toPx(sprite.x, sprite.y, lay);
    if (Math.hypot(x - p.x - 8, y - p.y) < 22) hoverId = employee.id;
  }
});
canvas.addEventListener("mouseleave", () => {
  hoverId = null;
});

canvas.addEventListener("click", (event) => {
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
