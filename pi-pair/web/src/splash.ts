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

function markPath(doc: Document, d: string, filled: boolean): SVGPathElement {
  const path = doc.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", filled ? "currentColor" : "none");
  if (!filled) {
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "3.4");
    path.setAttribute("stroke-linecap", "round");
  }
  return path;
}

/** Two facing arcs and a spark. Original mark, not a circle-and-slash logo. */
export function appendBrandMark(doc: Document, host: HTMLElement): void {
  const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("aria-hidden", "true");
  svg.appendChild(markPath(doc, "M18 16c12 6 18 12 18 16s-6 10-18 16", false));
  svg.appendChild(markPath(doc, "M46 16c-12 6-18 12-18 16s6 10 18 16", false));
  svg.appendChild(markPath(doc, "M32 26.2l1.35 4.6 4.7 1.2-4.7 1.2L32 37.8l-1.35-4.6-4.7-1.2 4.7-1.2z", true));
  host.replaceChildren(svg);
}
