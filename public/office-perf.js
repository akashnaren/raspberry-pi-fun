/** Pi 3 frame policy. DPR stays 1. The rAF loop stops when the room is still. */

/** Ignore devicePixelRatio. A 2x backing store blows a 905MiB Pi for no HDMI gain. */
export function dprFor(_devicePixelRatio = 1) {
  return 1;
}

/**
 * True only when `now` and `born` share one clock and the bubble is still on screen.
 * A Date.now() birth compared with an animation-frame timestamp is negative and must
 * not count — that mismatch used to keep talk hot for the life of the kiosk page.
 */
export function bubbleAlive(now, born, life) {
  const age = now - (born || 0);
  return age >= 0 && age < (life || 0);
}

/**
 * idle/hover: draw once, then a blink timer.
 * walk/talk: vsync, widened to 33ms if the last frame missed a 12ms budget.
 * type: low rate, the hands only, and only for TYPE_BURST_MS.
 */
export function hotKind({ hover = false, bubble = null, now = 0, sprites = [] } = {}) {
  const list = sprites?.values ? [...sprites.values()] : [...(sprites || [])];
  for (const sprite of list) {
    if (sprite?.path?.length || sprite?.pose === "walk") return "walk";
  }
  if (bubble && bubbleAlive(now, bubble.born, bubble.life)) return "talk";
  for (const sprite of list) {
    if (sprite?.pose === "sit-type" || sprite?.pose === "type") return "type";
  }
  if (hover) return "hover";
  return "idle";
}

/** 0 = do not schedule another frame. */
export function frameGapMs(kind, lastFrameMs = 0) {
  if (kind === "idle" || kind === "hover") return 0;
  if (kind === "type") return 140;
  if (lastFrameMs > 12) return 33;
  return 16;
}

/**
 * How to arm the next paint.
 * vsync (gap ≤ 16): one animation frame, so a walk stays on the display clock.
 * wider gaps: wait the remainder. Do not chain rAF while the paint is not due —
 * an early requestAnimationFrame keeps the Pi compositor awake at refresh.
 */
export function armFrame({ kind, lastFrameMs = 0, sinceDrawMs = Infinity } = {}) {
  const gap = frameGapMs(kind, lastFrameMs);
  if (gap <= 0) return { mode: "idle", gap: 0, waitMs: 0 };
  const since = Number.isFinite(sinceDrawMs) ? sinceDrawMs : Infinity;
  if (since >= gap || gap <= 16) return { mode: "frame", gap, waitMs: 0 };
  return { mode: "wait", gap, waitMs: Math.max(0, gap - since) };
}

/** Hands move for a moment after a type event, then the pose is allowed to settle. */
export const TYPE_BURST_MS = 1800;

export function typingPose(sprite, now, burstMs = TYPE_BURST_MS) {
  if (!sprite || (sprite.pose !== "type" && sprite.pose !== "sit-type")) {
    return { pose: sprite?.pose || "idle", frame: sprite?.frame || 0, typeUntil: sprite?.typeUntil || 0, hot: false };
  }
  const until = sprite.typeUntil || now + burstMs;
  if (now >= until) return { pose: "settle", frame: 0, typeUntil: 0, hot: false };
  return { pose: sprite.pose, frame: Math.floor(now / 220) % 2, typeUntil: until, hot: true };
}

/** In-page readout for ?perf=1. Idle is zero frames — the chip must not schedule its own loop. */
export function perfReadout({ kind = "idle", gap = 0, frames = 0, spanMs = 0 } = {}) {
  const still = kind === "idle" || kind === "hover" || gap <= 0;
  const fps = !still && spanMs > 0 ? Math.round((frames * 1000) / spanMs) : 0;
  const shownGap = still ? 0 : gap;
  const label = still ? "idle" : kind;
  return { fps, gap: shownGap, text: `${label} · ${fps} fps · gap ${shownGap}` };
}

export function dueBlink(now, sprites) {
  const list = sprites?.values ? [...sprites.values()] : [...(sprites || [])];
  if (!list.length) return { delay: 4200, index: -1 };
  let delay = 4200;
  let index = 0;
  for (let i = 0; i < list.length; i += 1) {
    const sprite = list[i];
    const period = 2600 + (Math.abs(Math.round((sprite.x || 1) * 400)) % 1800);
    const elapsed = sprite.blinkUntil ? now - sprite.blinkUntil : 0;
    const remain = Math.max(0, period - elapsed);
    if (remain <= delay) {
      delay = remain;
      index = i;
    }
  }
  return { delay: Math.max(90, delay), index };
}
