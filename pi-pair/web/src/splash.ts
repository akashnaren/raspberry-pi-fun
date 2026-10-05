/** First-load mark. Once per tab, and again when the document is reloaded. */

export const SPLASH_KEY = "openpi.splash";
export const SPLASH_HOLD_MS = 840;

export function shouldPlaySplash(seen: string | null, navType: string): boolean {
  if (navType === "reload") return true;
  return seen !== "1";
}

export function navigationType(): string {
  try {
    const entries = performance.getEntriesByType("navigation") as PerformanceNavigationTiming[];
    const kind = entries && entries[0] ? entries[0].type : "";
    return kind || "navigate";
  } catch {
    return "navigate";
  }
}

/** Crossed arcs and a spark. Original mark, not a circle-and-slash logo. */
export function appendBrandMark(doc: Document, host: HTMLElement): void {
  const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("aria-hidden", "true");
  const left = doc.createElementNS("http://www.w3.org/2000/svg", "path");
  left.setAttribute("d", "M14 46c2-14 12-24 22-22");
  left.setAttribute("fill", "none");
  left.setAttribute("stroke", "currentColor");
  left.setAttribute("stroke-width", "3.2");
  left.setAttribute("stroke-linecap", "round");
  const right = doc.createElementNS("http://www.w3.org/2000/svg", "path");
  right.setAttribute("d", "M50 18c-2 14-12 24-22 22");
  right.setAttribute("fill", "none");
  right.setAttribute("stroke", "currentColor");
  right.setAttribute("stroke-width", "3.2");
  right.setAttribute("stroke-linecap", "round");
  const spark = doc.createElementNS("http://www.w3.org/2000/svg", "path");
  spark.setAttribute("d", "M32 24.2l1.5 5.1 5.3 1.5-5.3 1.5-1.5 5.1-1.5-5.1-5.3-1.5 5.3-1.5z");
  spark.setAttribute("fill", "currentColor");
  svg.appendChild(left);
  svg.appendChild(right);
  svg.appendChild(spark);
  host.replaceChildren(svg);
}
