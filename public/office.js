import {
  companionId,
  destinationFor,
  fitView,
  hudStatus,
  latestEventLine,
  poseFor,
  settlePose,
  standBeside,
  verbFor,
} from "./office-motion.js";
import { FALLBACK_CAST, FALLBACK_OFFICE, seedSprites } from "./office-seed.js";
import { drawBubble, drawOffice, drawPerson, fillVoid, staticRoomKey } from "./office-draw.js";
import { dprFor, dueBlink, frameGapMs, hotKind } from "./office-perf.js";
import { mergeStudioState, parseSocketMessage, reconnectDelayMs } from "./office-net.js";

const canvas = document.getElementById("office");
const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
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
const pauseNote = document.getElementById("pause-note");

let CELL = 30;
const DPR = dprFor();
const ENTER = 220;
const HOLD = 4200;
const FADE = 700;

let state = {
  office: FALLBACK_OFFICE,
  employees: FALLBACK_CAST,
  events: [],
  sprites: seedSprites(),
  dryRun: true,
  paused: false,
  replay: false,
  sleeping: false,
  lite: false,
  budget: null,
  hud: null,
  acting: null,
  relationships: null,
  studio: { product: "Meridian Office" },
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
let rafId = 0;
let idleTimer = 0;
let lastFrameMs = 0;
let roomCanvas = null;
let roomCtx = null;
let roomKey = "";

let wsAttempt = 0;
let httpAttempt = 0;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.addEventListener("open", () => {
    wsAttempt = 0;
  });
  socket.addEventListener("message", (event) => {
    const msg = parseSocketMessage(event.data);
    if (!msg) return;
    if (msg.type === "hello" || msg.type === "event" || msg.type === "replay") {
      applyState(msg.state, msg.event);
    }
    if (msg.type === "validate") runValidation(socket, msg);
  });
  socket.addEventListener("close", () => {
    const wait = reconnectDelayMs(wsAttempt);
    wsAttempt += 1;
    setTimeout(connect, wait);
  });
}

async function hydrateFromHttp() {
  try {
    const res = await fetch("/api/state");
    if (!res.ok) throw new Error(`state ${res.status}`);
    applyState(await res.json());
    httpAttempt = 0;
  } catch {
    // Room already exists from FALLBACK_OFFICE. Retry with backoff.
    const wait = reconnectDelayMs(httpAttempt);
    httpAttempt += 1;
    setTimeout(hydrateFromHttp, wait);
  }
}

function applyState(next, event) {
  if (!next) return;
  const sprites = state.sprites;
  const startedAt = state.startedAt;
  state = mergeStudioState({ ...state, sprites, startedAt }, next, FALLBACK_OFFICE, FALLBACK_CAST);
  state.sprites = sprites;
  state.startedAt = startedAt;
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
  productTitle.textContent = next.studio?.product || "Meridian Office";
  const mode = next.mode || (next.dryRun ? "dry-run" : "live");
  modeBadge.textContent = mode;
  document.body.classList.toggle("sleeping", sleeping);
  document.body.classList.toggle("paused", Boolean(next.paused) && !sleeping);
  document.body.classList.toggle("lite", Boolean(next.lite) || new URLSearchParams(location.search).has("lite"));
  pauseBadge.classList.toggle("hidden", !next.paused || sleeping);
  sleepBadge.classList.toggle("hidden", !sleeping);
  if (pauseNote) pauseNote.classList.toggle("hidden", !next.paused || sleeping);
  pauseBtn.textContent = next.paused ? "resume" : "pause";
  pauseBtn.setAttribute("aria-label", next.paused ? "Resume the office" : "Pause the office");
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
  wake();
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
        pose: "sit",
        facing: 1,
        frame: 0,
        at: "desk",
        active: 0,
        blinkUntil: 0,
      });
    }
  }
}

function enqueueWalk(sprite, target) {
  if (!sprite || !target) return;
  const from = sprite.path?.length
    ? sprite.path[sprite.path.length - 1]
    : { x: Math.round(sprite.x), y: Math.round(sprite.y) };
  if (from.x === target.x && from.y === target.y) {
    sprite.at = target.at;
    if (!sprite.path?.length) sprite.pose = settlePose(target.at, sprite.wantPose || "idle");
    return;
  }
  const next = findPath(state.office, from, target);
  sprite.path = [...(sprite.path || []), ...next];
  sprite.at = target.at;
  sprite.pose = sprite.path.length ? "walk" : settlePose(target.at, sprite.wantPose || "idle");
}

function react(event) {
  const sprite = state.sprites.get(event.actor);
  const target = destinationFor(event, state.office);
  if (sprite && target) {
    sprite.wantPose = poseFor(event);
    sprite.active = 1;
    enqueueWalk(sprite, target);
  }
  const otherId = companionId(event);
  if (otherId && target && otherId !== event.actor) {
    const buddy = state.sprites.get(otherId);
    if (buddy) {
      buddy.wantPose = "talk";
      buddy.active = 1;
      enqueueWalk(buddy, standBeside(target, 1, 0));
    }
  }
  for (const [id, other] of state.sprites) {
    if (id !== event.actor && id !== otherId) other.active = Math.max(0, other.active - 0.4);
  }
  if (event.type === "say" && event.data?.text) {
    const who = (state.employees || []).find((person) => person.id === event.actor);
    bubble = {
      actor: event.actor,
      lines: wrapTwo(event.data.text),
      born: Date.now(),
      color: who?.accent || who?.color || "#c4b8a8",
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
    if (item.kind === "rug" || item.kind === "window") continue;
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
  view = fitView(state.office || FALLBACK_OFFICE, canvas.width / DPR, canvas.height / DPR);
  CELL = view.cell;
}

function facePairs() {
  const list = [...state.sprites.entries()];
  for (const [id, sprite] of list) {
    if (!sprite.at || sprite.at === "desk") continue;
    const other = list.find(([, mate]) => mate !== sprite && mate.at === sprite.at);
    if (!other) continue;
    sprite.facing = other[1].x >= sprite.x ? 1 : -1;
  }
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
        if (!sprite.path.length) sprite.pose = settlePose(sprite.at, sprite.wantPose || "idle");
      } else {
        const step = Math.min(dist, speed * dt);
        sprite.x += (dx / dist) * step;
        sprite.y += (dy / dist) * step;
        sprite.facing = dx < 0 ? -1 : 1;
        sprite.pose = "walk";
        sprite.frame = Math.floor(now / 140) % 2;
      }
    } else if (sprite.pose === "type" || sprite.pose === "sit-type") {
      sprite.frame = Math.floor(now / 220) % 2;
    }
  }
  return walking;
}

function employeeModel(employee) {
  if (!employee) return "";
  return employee.model || employee.modelFamily || "";
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

function viewSize() {
  return { w: canvas.width / DPR, h: canvas.height / DPR };
}

function currentHot(now) {
  const alive = bubble && now - bubble.born < ENTER + HOLD + FADE;
  return hotKind({
    hover: Boolean(hoverId),
    bubble: alive ? { born: bubble.born, life: ENTER + HOLD + FADE } : null,
    now,
    sprites: state.sprites,
  });
}

function clearIdle() {
  if (!idleTimer) return;
  clearTimeout(idleTimer);
  idleTimer = 0;
}

function wake() {
  clearIdle();
  if (!rafId) rafId = requestAnimationFrame(loop);
}

function scheduleIdle(now) {
  clearIdle();
  const kind = currentHot(now);
  if (frameGapMs(kind, lastFrameMs) > 0) {
    wake();
    return;
  }
  if (state.paused || state.sleeping || state.replay || state.budget?.exhausted) {
    const remain = Math.max(250, 6000 - (now - lastReplay));
    idleTimer = setTimeout(() => {
      idleTimer = 0;
      maybeReplay(performance.now());
      wake();
    }, remain);
    return;
  }
  const due = dueBlink(now, state.sprites);
  idleTimer = setTimeout(() => {
    idleTimer = 0;
    const list = [...state.sprites.values()];
    const sprite = list[dueBlink(performance.now(), state.sprites).index];
    if (sprite && sprite.pose !== "walk") sprite.blinkUntil = performance.now() + 130;
    wake();
  }, due.delay);
}

function paintRoom(w, h, dim) {
  const office = state.office || FALLBACK_OFFICE;
  const employees = state.employees || FALLBACK_CAST;
  const key = staticRoomKey({
    office,
    cell: CELL,
    dim,
    w,
    h,
    ox: view.ox,
    oy: view.oy,
    employees,
  });
  if (!roomCanvas) {
    roomCanvas = document.createElement("canvas");
    roomCtx = roomCanvas.getContext("2d", { alpha: false });
  }
  if (roomKey !== key) {
    roomCanvas.width = Math.max(1, canvas.width);
    roomCanvas.height = Math.max(1, canvas.height);
    roomCtx.setTransform(DPR, 0, 0, DPR, 0, 0);
    fillVoid(roomCtx, w, h);
    drawOffice(roomCtx, { office, employees, ox: view.ox, oy: view.oy, cell: CELL, dim });
    roomKey = key;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(roomCanvas, 0, 0);
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
}

function draw(now) {
  const { w, h } = viewSize();
  const ox = view.ox;
  const oy = view.oy;
  const dim = Boolean(state.paused || state.sleeping || state.budget?.exhausted);
  paintRoom(w, h, dim);
  const walking = stepSprites(now);
  facePairs();
  const people = [...(state.employees || FALLBACK_CAST)].sort(
    (a, b) => (state.sprites.get(a.id)?.y || 0) - (state.sprites.get(b.id)?.y || 0),
  );
  if (dim) ctx.globalAlpha = 0.78;
  for (const employee of people) {
    const sprite = state.sprites.get(employee.id);
    if (!sprite) continue;
    drawPerson(ctx, {
      employee,
      sprite,
      ox,
      oy,
      cell: CELL,
      now,
      hover: hoverId === employee.id,
      feel: feelLine(employee.id),
      verb: hoverId === employee.id ? verbFor(sprite.pose, sprite.at) : "",
    });
  }
  ctx.globalAlpha = 1;
  const talking = drawBubble(ctx, {
    bubble,
    sprite: bubble ? state.sprites.get(bubble.actor) : null,
    ox,
    oy,
    cell: CELL,
    now,
    enter: ENTER,
    hold: HOLD,
    fade: FADE,
  });
  if (bubble && !talking) bubble = null;
  return walking || talking;
}

function loop(ts) {
  const started = performance.now();
  rafId = 0;
  const kind = currentHot(ts);
  const gap = frameGapMs(kind, lastFrameMs);
  if (gap > 0 && lastDraw && ts - lastDraw < gap) {
    rafId = requestAnimationFrame(loop);
    return;
  }
  draw(ts);
  lastDraw = ts;
  lastFrameMs = performance.now() - started;
  const again = frameGapMs(currentHot(performance.now()), lastFrameMs);
  if (again > 0) {
    rafId = requestAnimationFrame(loop);
    return;
  }
  let blinkLeft = 0;
  for (const sprite of state.sprites.values()) {
    if (sprite.blinkUntil > ts) blinkLeft = Math.max(blinkLeft, sprite.blinkUntil - ts);
  }
  if (blinkLeft > 0) {
    clearIdle();
    idleTimer = setTimeout(() => {
      idleTimer = 0;
      wake();
    }, blinkLeft + 16);
    return;
  }
  scheduleIdle(ts);
}

canvas.addEventListener("mousemove", (event) => {
  const rect = canvas.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / rect.width) * (canvas.width / DPR);
  const y = ((event.clientY - rect.top) / rect.height) * (canvas.height / DPR);
  const prev = hoverId;
  hoverId = null;
  const ox = view.ox;
  const oy = view.oy;
  for (const employee of state.employees || []) {
    const sprite = state.sprites.get(employee.id);
    if (!sprite) continue;
    const px = ox + sprite.x * CELL + CELL * 0.35;
    const py = oy + sprite.y * CELL + CELL * 0.15;
    if (Math.hypot(x - px, y - py) < CELL * 0.85) hoverId = employee.id;
  }
  if (hoverId !== prev) wake();
});
canvas.addEventListener("mouseleave", () => {
  if (!hoverId) return;
  hoverId = null;
  wake();
});

canvas.addEventListener("click", () => {
  const inspect = document.getElementById("inspect");
  const title = document.getElementById("inspect-title");
  const body = document.getElementById("inspect-body");
  if (hoverId) {
    const person = (state.employees || []).find((item) => item.id === hoverId);
    const modelEl = document.getElementById("inspect-model");
    title.textContent = person?.name || hoverId;
    if (modelEl) modelEl.textContent = employeeModel(person);
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

if (new URLSearchParams(location.search).get("bench") === "walk") {
  window.__benchWalk = () => {
    for (const sprite of state.sprites.values()) {
      const x = Math.round(sprite.x);
      const y = Math.round(sprite.y);
      sprite.path = [
        { x, y: y + 1 },
        { x: x + 1, y: y + 2 },
        { x, y: y + 3 },
      ];
      sprite.pose = "walk";
      sprite.at = "coffee";
    }
    wake();
  };
}

window.addEventListener("resize", () => {
  resize();
  wake();
});
resize();
syncSprites();
requestAnimationFrame(loop);
hydrateFromHttp();
connect();
