/** A background tab is not a dropped connection. */

export const RESUME_GRACE_MS = 2500;

export function suppressOfflineBanner(
  pageHidden: boolean,
  resumedAt: number,
  now: number,
  grace = RESUME_GRACE_MS,
): boolean {
  if (pageHidden) return true;
  return resumedAt > 0 && now - resumedAt < grace;
}
