/** Composer dictation mic. Idle is gray; listening is a mint disc. */

export const MIC_OFF_CLASS = "mic-off";
export const MIC_ON_CLASS = "mic-on";

/** Idle icon. Same gray as the other composer tools at rest. */
export const MIC_OFF_COLOR = "#7d8187";
/** Listening icon, drawn on the mint disc. */
export const MIC_ON_COLOR = "#042117";
/** Listening disc. Opaque mint, not the idle gray. */
export const MIC_ON_FILL = "#3ddc97";
/** Listening disc while the pointer is over it. */
export const MIC_ON_HOVER_FILL = "#2ecf8a";

export function micStateClasses(active: boolean): { add: string; remove: string } {
  if (active) return { add: MIC_ON_CLASS, remove: MIC_OFF_CLASS };
  return { add: MIC_OFF_CLASS, remove: MIC_ON_CLASS };
}

export function paintMicButton(button: HTMLElement, active: boolean): void {
  const next = micStateClasses(active);
  button.classList.add(next.add);
  button.classList.remove(next.remove);
  button.classList.toggle("on", active);
}
