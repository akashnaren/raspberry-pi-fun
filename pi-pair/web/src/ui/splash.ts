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

function svgEl<T extends SVGElement>(doc: Document, name: string, attrs: Record<string, string>): T {
  const node = doc.createElementNS("http://www.w3.org/2000/svg", name) as T;
  Object.entries(attrs).forEach(([key, value]) => {
    node.setAttribute(key, value);
  });
  return node;
}

/**
 * Pi glyph inside a broken orbit. Same geometry as favicon.svg.
 * Strokes use a teal-to-blue gradient so the mark reads on dark and light.
 */
export function appendBrandMark(doc: Document, host: HTMLElement): void {
  const svg = svgEl<SVGSVGElement>(doc, "svg", {
    viewBox: "0 0 64 64",
    "aria-hidden": "true",
  });
  const defs = svgEl<SVGDefsElement>(doc, "defs", {});
  const grad = svgEl<SVGLinearGradientElement>(doc, "linearGradient", {
    id: "openpiMark",
    x1: "8",
    y1: "4",
    x2: "56",
    y2: "60",
    gradientUnits: "userSpaceOnUse",
  });
  grad.appendChild(svgEl(doc, "stop", { class: "mark-a", offset: "0", "stop-color": "#5eead4" }));
  grad.appendChild(svgEl(doc, "stop", { class: "mark-b", offset: "1", "stop-color": "#7cb8ff" }));
  defs.appendChild(grad);
  svg.appendChild(defs);
  const stroke = {
    fill: "none",
    stroke: "url(#openpiMark)",
    "stroke-width": "4.4",
    "stroke-linecap": "round",
  };
  svg.appendChild(svgEl(doc, "circle", {
    cx: "32",
    cy: "32",
    r: "27.5",
    fill: "none",
    stroke: "url(#openpiMark)",
    "stroke-width": "1.7",
    "stroke-linecap": "round",
    "stroke-dasharray": "128 12 20 14",
  }));
  svg.appendChild(svgEl(doc, "circle", { class: "mark-node", cx: "32", cy: "4.5", r: "1.8", fill: "#5eead4" }));
  svg.appendChild(svgEl(doc, "circle", { class: "mark-node", cx: "57.2", cy: "42", r: "1.6", fill: "#7cb8ff" }));
  svg.appendChild(svgEl(doc, "circle", { class: "mark-node", cx: "8.4", cy: "46", r: "1.5", fill: "#7ec8b8" }));
  svg.appendChild(svgEl(doc, "path", { ...stroke, d: "M18 23h28" }));
  svg.appendChild(svgEl(doc, "path", { ...stroke, d: "M26.2 23c0 13-1.4 18.2-6.4 21.2" }));
  svg.appendChild(svgEl(doc, "path", { ...stroke, d: "M38.2 23c.6 14.2 2.4 19.6 8.4 21.6" }));
  host.replaceChildren(svg);
}
