const canvas = document.getElementById("office");
const ctx = canvas.getContext("2d");
const ticker = document.getElementById("ticker");
const speech = document.getElementById("speech");
const product = document.getElementById("product");
const validator = document.getElementById("validator");
const pauseBtn = document.getElementById("pause-btn");
const pauseBadge = document.getElementById("pause-badge");
const modeBadge = document.getElementById("mode-badge");
const dayNum = document.getElementById("day-num");
const headcount = document.getElementById("headcount");
const budgetEl = document.getElementById("budget");

const CELL = 28;
let state = {
  office: null,
  employees: [],
  events: [],
  sprites: new Map(),
  dryRun: true,
  paused: false,
  startedAt: Date.now(),
};
let bubbles = [];

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  socket.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "hello" || msg.type === "event") applyState(msg.state, msg.event);
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
  headcount.textContent = String((next.employees || []).length);
  if (next.budget) {
    const left = next.budget.remainingUsd;
    budgetEl.textContent = `$${left.toFixed(2)} left`;
  }
  modeBadge.textContent = next.dryRun ? "dry-run" : "live";
  modeBadge.classList.toggle("live", !next.dryRun);
  pauseBadge.classList.toggle("hidden", !next.paused);
  pauseBtn.textContent = next.paused ? "Resume" : "Pause";
  if (next.events?.length) {
    ticker.textContent = next.events
      .slice()
      .reverse()
      .map((item) => item.message)
      .slice(0, 4)
      .join("  ·  ");
  }
  if (event) react(event);
  if (event?.type === "build_passed") {
    product.src = `/dist/index.html?t=${event.ts}`;
  }
  syncSprites();
}

function deskFor(id) {
  return (state.office?.desks || []).find((desk) => desk.owner === id);
}

function syncSprites() {
  for (const employee of state.employees || []) {
    const desk = deskFor(employee.id);
    if (!state.sprites.has(employee.id)) {
      state.sprites.set(employee.id, {
        x: (desk?.x ?? 4) + 0.4,
        y: (desk?.y ?? 4) + 1.2,
        tx: (desk?.x ?? 4) + 0.4,
        ty: (desk?.y ?? 4) + 1.2,
        pulse: 0,
      });
    }
  }
}

function targetFor(event) {
  const office = state.office;
  if (!office) return null;
  if (event.type === "task_added" || event.type === "task_closed") {
    const board = (office.decor || []).find((item) => item.kind === "whiteboard");
    if (board) return { x: board.x + 0.4, y: board.y + 1.2 };
  }
  const desk = deskFor(event.actor);
  if (desk) return { x: desk.x + 0.4, y: desk.y + 1.2 };
  return null;
}

function react(event) {
  const sprite = state.sprites.get(event.actor);
  const target = targetFor(event);
  if (sprite && target) {
    sprite.tx = target.x;
    sprite.ty = target.y;
    sprite.pulse = 1;
  }
  if (event.type === "say" && event.data?.text) {
    bubbles = [
      { actor: event.actor, text: event.data.text, until: Date.now() + 8000 },
      ...bubbles.filter((item) => item.actor !== event.actor),
    ].slice(0, 3);
    const who = (state.employees || []).find((person) => person.id === event.actor);
    speech.hidden = false;
    speech.textContent = `${who?.name || event.actor}: ${event.data.text}`;
  }
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
      if (!doc || !doc.documentElement) {
        finish(false, "empty document");
        return;
      }
      const err = doc.querySelector("parsererror");
      if (err) {
        finish(false, "parsererror");
        return;
      }
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
  window.addEventListener(
    "message",
    (event) => {
      if (event.data?.type === "build_error") {
        clearTimeout(timer);
        finish(false, event.data.message || "runtime error");
      }
    },
    { once: true },
  );
  validator.src = msg.url;
}

function resize() {
  const pane = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(pane.clientWidth * dpr);
  canvas.height = Math.floor(pane.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function draw() {
  const w = canvas.width / (window.devicePixelRatio || 1);
  const h = canvas.height / (window.devicePixelRatio || 1);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#14141b";
  ctx.fillRect(0, 0, w, h);

  const office = state.office;
  const ox = 24;
  const oy = 20;
  if (office) {
    ctx.fillStyle = office.walls || "#1e1e26";
    const bounds = office.rooms?.reduce(
      (acc, room) => ({
        w: Math.max(acc.w, room.x + room.w),
        h: Math.max(acc.h, room.y + room.h),
      }),
      { w: 22, h: 16 },
    ) || { w: 22, h: 16 };
    ctx.fillRect(ox, oy, bounds.w * CELL, bounds.h * CELL);
    ctx.fillStyle = "#20202a";
    for (let x = 0; x < bounds.w; x += 1) {
      for (let y = 0; y < bounds.h; y += 1) {
        if ((x + y) % 2 === 0) ctx.fillRect(ox + x * CELL, oy + y * CELL, CELL, CELL);
      }
    }
    for (const room of office.rooms || []) {
      ctx.strokeStyle = "#3a3a46";
      ctx.strokeRect(ox + room.x * CELL, oy + room.y * CELL, room.w * CELL, room.h * CELL);
      ctx.fillStyle = "#8d877c";
      ctx.font = "11px sans-serif";
      ctx.fillText(room.name, ox + room.x * CELL + 6, oy + room.y * CELL + 14);
    }
    for (const decor of office.decor || []) {
      ctx.fillStyle = decor.kind === "whiteboard" ? "#d9d3c5" : "#6b4a2f";
      ctx.fillRect(ox + decor.x * CELL, oy + decor.y * CELL, CELL * 2.2, CELL * 0.8);
      ctx.fillStyle = "#2b2418";
      ctx.font = "10px sans-serif";
      ctx.fillText(decor.text || decor.kind, ox + decor.x * CELL + 6, oy + decor.y * CELL + 14);
    }
    for (const desk of office.desks || []) {
      ctx.fillStyle = "#3b2e22";
      ctx.fillRect(ox + desk.x * CELL, oy + desk.y * CELL, CELL * 2.2, CELL * 1.4);
      ctx.fillStyle = "#6d5a45";
      ctx.fillRect(ox + desk.x * CELL + 6, oy + desk.y * CELL + 6, CELL * 0.9, CELL * 0.45);
    }
  }

  const now = Date.now();
  for (const employee of state.employees || []) {
    const sprite = state.sprites.get(employee.id);
    if (!sprite) continue;
    sprite.x += (sprite.tx - sprite.x) * 0.08;
    sprite.y += (sprite.ty - sprite.y) * 0.08;
    sprite.pulse *= 0.96;
    const x = ox + sprite.x * CELL;
    const y = oy + sprite.y * CELL;
    ctx.fillStyle = employee.color || "#888";
    ctx.fillRect(x, y, 22, 22);
    if (sprite.pulse > 0.05) {
      ctx.strokeStyle = employee.color || "#888";
      ctx.globalAlpha = sprite.pulse;
      ctx.strokeRect(x - 3, y - 3, 28, 28);
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = "#f3eee4";
    ctx.font = "11px sans-serif";
    ctx.fillText(employee.name, x - 2, y + 36);
    ctx.fillStyle = "#9a9488";
    ctx.font = "9px sans-serif";
    ctx.fillText(employee.role, x - 2, y + 48);
  }

  bubbles = bubbles.filter((item) => item.until > now);
  for (const bubble of bubbles) {
    const sprite = state.sprites.get(bubble.actor);
    if (!sprite) continue;
    const x = ox + sprite.x * CELL + 26;
    const y = oy + sprite.y * CELL - 28;
    ctx.fillStyle = "#2a2a34";
    ctx.fillRect(x, y, 160, 24);
    ctx.fillStyle = "#ece7dc";
    ctx.font = "10px sans-serif";
    ctx.fillText(bubble.text.slice(0, 28), x + 6, y + 16);
  }

  if (!speech.hidden && bubbles.length === 0) speech.hidden = true;
  const start = state.startedAt || now;
  dayNum.textContent = String(1 + Math.floor((now - start) / 86_400_000));
  requestAnimationFrame(draw);
}

pauseBtn.addEventListener("click", async () => {
  const path = state.paused ? "/api/resume" : "/api/pause";
  await fetch(path, { method: "POST" });
});

window.addEventListener("resize", resize);
resize();
syncSprites();
connect();
draw();
