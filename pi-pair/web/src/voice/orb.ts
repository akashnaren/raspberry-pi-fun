/** Canvas voice orb. The loop runs only while a voice state is on screen. */

export type VoiceOrbState = "idle" | "listening" | "heard" | "thinking" | "speaking" | "error";

export interface VoiceOrb {
  set(state: VoiceOrbState): void;
  level(amount: number): void;
  pulse(): void;
  destroy(): void;
  state(): VoiceOrbState;
}

interface OrbLook {
  radius: number;
  glow: number;
  lobe: number;
  orbit: number;
  alpha: number;
}

interface OrbOptions {
  sampleLevel?: () => number;
}

const LOOK: Record<VoiceOrbState, OrbLook> = {
  idle: { radius: 0.2, glow: 0, lobe: 0, orbit: 0, alpha: 0 },
  listening: { radius: 0.72, glow: 0.35, lobe: 0, orbit: 0, alpha: 1 },
  heard: { radius: 0.78, glow: 0.55, lobe: 0.04, orbit: 0, alpha: 1 },
  thinking: { radius: 0.7, glow: 0.45, lobe: 0, orbit: 1, alpha: 1 },
  speaking: { radius: 0.74, glow: 0.6, lobe: 0.22, orbit: 0, alpha: 1 },
  error: { radius: 0.68, glow: 0.12, lobe: 0, orbit: 0, alpha: 0.45 },
};

function easeOutCubic(amount: number): number {
  const t = Math.min(1, Math.max(0, amount));
  return 1 - (1 - t) ** 3;
}

function mix(from: number, to: number, t: number): number {
  return from + (to - from) * t;
}

function prefersStill(): boolean {
  const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  return Boolean(media && media.matches);
}

function cssVar(name: string, fallback: string): string {
  const styles = globalThis.getComputedStyle?.(document.documentElement);
  const value = styles?.getPropertyValue(name).trim();
  return value || fallback;
}

function nowMs(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

export function createOrb(canvas: HTMLCanvasElement, options: OrbOptions = {}): VoiceOrb {
  const context = typeof canvas.getContext === "function" ? canvas.getContext("2d") : null;
  let state: VoiceOrbState = "idle";
  let from = { ...LOOK.idle };
  let current = { ...LOOK.idle };
  let target = { ...LOOK.idle };
  let tweenAt = 0;
  let rafId = 0;
  let looping = false;
  let destroyed = false;
  let levelNow = 0;
  let levelTarget = 0;
  let envelope = 0;
  let errorAt = 0;
  let lastFrame = 0;
  let ink = "#ececec";
  let accent = "#7cb8ff";
  let bg = "#0a0a0a";
  let still = prefersStill();

  const onMotion = () => {
    still = prefersStill();
  };
  const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  motion?.addEventListener?.("change", onMotion);

  const onVisibility = () => {
    if (document.visibilityState === "visible") arm();
  };
  document.addEventListener?.("visibilitychange", onVisibility);

  function readColors(): void {
    accent = cssVar("--accent", "#7cb8ff");
    ink = cssVar("--fg", cssVar("--ink", "#ececec"));
    bg = cssVar("--bg", "#0a0a0a");
  }

  function hidden(): boolean {
    return document.visibilityState === "hidden";
  }

  function arm(): void {
    if (destroyed || looping || state === "idle" || hidden()) return;
    if (typeof requestAnimationFrame !== "function") return;
    looping = true;
    rafId = requestAnimationFrame(tick);
  }

  function stop(): void {
    looping = false;
    lastFrame = 0;
    if (rafId && typeof cancelAnimationFrame === "function") cancelAnimationFrame(rafId);
    rafId = 0;
  }

  function tick(now: number): void {
    if (!looping || destroyed) return;
    if (state === "idle" || hidden()) {
      stop();
      return;
    }
    const dt = lastFrame ? Math.min(48, now - lastFrame) : 16;
    lastFrame = now;
    const t = easeOutCubic((now - tweenAt) / 240);
    current = {
      radius: mix(from.radius, target.radius, t),
      glow: mix(from.glow, target.glow, t),
      lobe: mix(from.lobe, target.lobe, t),
      orbit: mix(from.orbit, target.orbit, t),
      alpha: mix(from.alpha, target.alpha, t),
    };
    const sampled = options.sampleLevel?.();
    if (typeof sampled === "number" && Number.isFinite(sampled)) {
      levelTarget = Math.min(1, Math.max(0, sampled));
    }
    const follow = levelTarget > levelNow ? 60 : 180;
    levelNow += (levelTarget - levelNow) * Math.min(1, dt / follow);
    envelope *= Math.exp(-dt / 220);
    draw(now);
    if (!looping || hidden()) {
      stop();
      return;
    }
    rafId = requestAnimationFrame(tick);
  }

  function draw(now: number): void {
    if (!context) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const size = 220;
    if (canvas.width !== Math.round(size * dpr)) {
      canvas.width = Math.round(size * dpr);
      canvas.height = Math.round(size * dpr);
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size, size);
    const cx = size / 2;
    const cy = size / 2;
    if (still) {
      let ringAlpha = 0.9;
      if (state === "idle") ringAlpha = 0;
      else if (state === "error") ringAlpha = 0.4;
      context.globalAlpha = ringAlpha;
      context.beginPath();
      context.arc(cx, cy, 72, 0, Math.PI * 2);
      context.strokeStyle = accent;
      context.lineWidth = 3;
      context.stroke();
      context.globalAlpha = 1;
      return;
    }
    let radius = 70 * current.radius;
    if (state === "listening") {
      radius *= 0.96 + 0.04 * (0.5 + 0.5 * Math.sin((now / 2400) * Math.PI * 2));
    }
    if (state === "heard") radius *= 0.92 + 0.16 * levelNow;
    let dx = 0;
    if (state === "error") {
      const age = now - errorAt;
      if (age >= 0 && age < 120) dx = Math.sin(age / 18) * 4;
    }
    const x = cx + dx;
    const shift = state === "thinking" ? Math.sin(now / 400) * radius * 0.18 : 0;
    context.globalAlpha = current.alpha;
    const gradient = context.createRadialGradient(
      x - radius * 0.3 + shift,
      cy - radius * 0.35,
      radius * 0.1,
      x,
      cy,
      Math.max(1, radius),
    );
    gradient.addColorStop(0, ink);
    gradient.addColorStop(0.55, state === "error" ? bg : accent);
    gradient.addColorStop(1, bg);
    context.beginPath();
    if (state === "speaking" && current.lobe > 0) {
      const amp = current.lobe * (0.35 + envelope);
      for (let step = 0; step <= 64; step += 1) {
        const angle = (step / 64) * Math.PI * 2;
        const wave = Math.sin(angle * 3 + now / 280) * amp;
        const r = radius * (1 + wave);
        const px = x + Math.cos(angle) * r;
        const py = cy + Math.sin(angle) * r;
        if (step === 0) context.moveTo(px, py);
        else context.lineTo(px, py);
      }
      context.closePath();
    } else {
      context.arc(x, cy, Math.max(1, radius), 0, Math.PI * 2);
    }
    context.shadowBlur = 28 * current.glow;
    context.shadowColor = accent;
    context.fillStyle = gradient;
    context.fill();
    context.shadowBlur = 0;
    if (state === "heard") {
      context.beginPath();
      context.arc(x, cy, radius + 8 + levelNow * 12, 0, Math.PI * 2);
      context.strokeStyle = accent;
      context.globalAlpha = 0.22 * current.alpha;
      context.lineWidth = 1.5;
      context.stroke();
    }
    if (state === "thinking" && current.orbit > 0.2) {
      const angle = (now / 1200) * Math.PI * 2;
      context.beginPath();
      context.arc(x, cy, radius + 10, angle, angle + 1.1);
      context.strokeStyle = accent;
      context.globalAlpha = current.alpha;
      context.lineWidth = 2;
      context.stroke();
    }
    context.globalAlpha = 1;
  }

  return {
    set(next: VoiceOrbState) {
      if (destroyed) return;
      if (next === state) {
        if (next !== "idle") arm();
        return;
      }
      from = { ...current };
      target = { ...LOOK[next] };
      tweenAt = nowMs();
      readColors();
      still = prefersStill();
      if (next === "error") errorAt = tweenAt;
      if (next !== "speaking") envelope = 0;
      state = next;
      if (next === "idle") {
        stop();
        context?.clearRect(0, 0, canvas.width, canvas.height);
        return;
      }
      arm();
    },
    level(amount: number) {
      if (!Number.isFinite(amount)) return;
      levelTarget = Math.min(1, Math.max(0, amount));
    },
    pulse() {
      envelope = Math.min(1, envelope + 0.35);
    },
    destroy() {
      destroyed = true;
      state = "idle";
      stop();
      document.removeEventListener?.("visibilitychange", onVisibility);
      motion?.removeEventListener?.("change", onMotion);
    },
    state() {
      return state;
    },
  };
}
