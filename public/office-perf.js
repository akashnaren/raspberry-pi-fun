/** Pi 3 frame policy. DPR stays 1. The rAF loop stops when the room is still. */

/** Ignore devicePixelRatio. A 2x backing store blows a 905MiB Pi for no HDMI gain. */
export function dprFor(_devicePixelRatio = 1) {
  return 1;
}

/**
 * idle/hover: draw once, then a blink timer.
 * walk/talk: vsync, widened to 33ms if the last frame missed a 12ms budget.
 * type: low rate, the hands only.
 */
export function hotKind({ hover = false, bubble = null, now = 0, sprites = [] } = {}) {
  const list = sprites?.values ? [...sprites.values()] : [...(sprites || [])];
  for (const sprite of list) {
    if (sprite?.path?.length || sprite?.pose === "walk") return "walk";
  }
  if (bubble && now - (bubble.born || 0) < (bubble.life || 0)) return "talk";
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
