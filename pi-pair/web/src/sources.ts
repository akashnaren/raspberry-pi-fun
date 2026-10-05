/** Post-answer sources pill and the right-hand sources panel. */

export interface SourceLink {
  title: string;
  url: string;
}

export interface PanelDetail {
  status: string;
  sources: SourceLink[];
  stages: string[];
  prompt: string;
}

export function sourceHost(url: string): string {
  try {
    const host = new URL(url).hostname;
    return host.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function sourceCountLabel(count: number): string {
  const n = Math.max(0, Math.floor(count));
  return n === 1 ? "1 source" : n + " sources";
}

export function validSources(sources: SourceLink[]): SourceLink[] {
  const out: SourceLink[] = [];
  (sources || []).forEach((src) => {
    const url = src && src.url ? String(src.url) : "";
    if (!url.startsWith("http")) return;
    const title = (src.title || url).trim() || url;
    out.push({ title, url });
  });
  return out;
}

export function faviconPlan(url: string): { google: string; local: string; letter: string } {
  const host = sourceHost(url);
  let origin = "";
  try {
    origin = new URL(url).origin;
  } catch {
    origin = "";
  }
  const letter = (host.match(/[a-z0-9]/i) || [""])[0].toUpperCase();
  return {
    google: "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(host || url) + "&sz=64",
    local: origin ? origin + "/favicon.ico" : "",
    letter,
  };
}

export function faviconStack(sources: SourceLink[]): SourceLink[] {
  const seen = new Set<string>();
  const stack: SourceLink[] = [];
  validSources(sources).forEach((src) => {
    if (stack.length >= 3) return;
    const host = sourceHost(src.url) || src.url;
    if (seen.has(host)) return;
    seen.add(host);
    stack.push(src);
  });
  return stack;
}

function svgEl(doc: Document, path: string): SVGSVGElement {
  const svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const shape = doc.createElementNS("http://www.w3.org/2000/svg", "path");
  shape.setAttribute("fill", "none");
  shape.setAttribute("stroke", "currentColor");
  shape.setAttribute("stroke-width", "1.6");
  shape.setAttribute("stroke-linecap", "round");
  shape.setAttribute("stroke-linejoin", "round");
  shape.setAttribute("d", path);
  svg.appendChild(shape);
  return svg;
}

function letterMark(doc: Document, letter: string): HTMLElement {
  const mark = doc.createElement("span");
  mark.className = "sources-fallback";
  if (letter) {
    mark.textContent = letter;
    return mark;
  }
  mark.appendChild(svgEl(doc, "M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zM4.8 12h14.4M12 4.6c2 2.2 2 12.6 0 14.8M12 4.6c-2 2.2-2 12.6 0 14.8"));
  return mark;
}

function faviconNode(doc: Document, src: SourceLink): HTMLElement {
  const wrap = doc.createElement("span");
  wrap.className = "sources-fav";
  const plan = faviconPlan(src.url);
  wrap.appendChild(letterMark(doc, plan.letter));
  if (!plan.google) return wrap;
  const img = doc.createElement("img");
  img.alt = "";
  img.src = plan.google;
  img.dataset.local = plan.local;
  img.dataset.letter = plan.letter;
  img.dataset.step = "google";
  img.style.opacity = "0";
  img.addEventListener("load", () => {
    if (img.naturalWidth > 0) img.style.opacity = "1";
  });
  img.addEventListener("error", () => {
    if (img.dataset.step !== "local" && plan.local) {
      img.dataset.step = "local";
      img.src = plan.local;
      return;
    }
    img.remove();
  });
  wrap.appendChild(img);
  return wrap;
}

export function renderFailedSearch(doc: Document): HTMLElement {
  const note = doc.createElement("div");
  note.className = "search-note search-failed";
  note.textContent = "Search failed";
  return note;
}

export function renderSourcesPill(doc: Document, sources: SourceLink[]): HTMLButtonElement {
  const links = validSources(sources);
  const button = doc.createElement("button");
  button.type = "button";
  button.className = "sources-pill";
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-expanded", "false");
  const stack = doc.createElement("span");
  stack.className = "sources-favs";
  stack.setAttribute("aria-hidden", "true");
  faviconStack(links).forEach((src) => stack.appendChild(faviconNode(doc, src)));
  const count = doc.createElement("span");
  count.className = "sources-count";
  count.textContent = sourceCountLabel(links.length);
  button.appendChild(stack);
  button.appendChild(count);
  button.setAttribute("aria-label", sourceCountLabel(links.length));
  return button;
}

export function renderSourcesPanelBody(doc: Document, detail: PanelDetail): HTMLElement {
  const body = doc.createElement("div");
  body.className = "sources-detail";
  const links = validSources(detail.sources);
  const steps = doc.createElement("ul");
  steps.className = "sources-steps";
  const stages = detail.stages || [];
  if (stages.includes("thinking")) {
    const row = doc.createElement("li");
    row.className = "sources-step";
    row.appendChild(svgEl(doc, "M9 18h6M10 21h4M12 3a6 6 0 0 0-3 11c.5.4.8 1 .9 1.6h4.2c.1-.6.4-1.2.9-1.6A6 6 0 0 0 12 3z"));
    const label = doc.createElement("span");
    label.textContent = "Thinking";
    row.appendChild(label);
    steps.appendChild(row);
  }
  if (detail.status === "failed") {
    const row = doc.createElement("li");
    row.className = "sources-step sources-step-failed";
    const label = doc.createElement("span");
    label.textContent = "Search failed";
    row.appendChild(label);
    steps.appendChild(row);
  } else if (links.length || stages.includes("searching") || detail.status === "ok") {
    const row = doc.createElement("li");
    row.className = "sources-step";
    row.appendChild(svgEl(doc, "M12 4.5a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15zM4.8 12h14.4M12 4.6c2 2.2 2 12.6 0 14.8M12 4.6c-2 2.2-2 12.6 0 14.8"));
    const copy = doc.createElement("div");
    copy.className = "sources-step-copy";
    const line = doc.createElement("div");
    line.className = "sources-step-line";
    const label = doc.createElement("span");
    label.textContent = "Searched web";
    const badge = doc.createElement("span");
    badge.className = "sources-badge";
    badge.textContent = String(links.length);
    line.appendChild(label);
    line.appendChild(badge);
    copy.appendChild(line);
    const query = (detail.prompt || "").trim();
    if (query) {
      const q = doc.createElement("div");
      q.className = "sources-query";
      q.textContent = query.length > 72 ? query.slice(0, 72) + "…" : query;
      copy.appendChild(q);
    }
    row.appendChild(copy);
    steps.appendChild(row);
  }
  body.appendChild(steps);
  if (links.length) {
    const list = doc.createElement("ul");
    list.className = "sources-links";
    links.forEach((src) => {
      const item = doc.createElement("li");
      const link = doc.createElement("a");
      link.href = src.url;
      link.target = "_blank";
      link.rel = "noopener";
      link.appendChild(faviconNode(doc, src));
      const copy = doc.createElement("span");
      copy.className = "sources-link-copy";
      const title = doc.createElement("span");
      title.className = "sources-title";
      title.textContent = src.title;
      const host = doc.createElement("span");
      host.className = "sources-host";
      host.textContent = sourceHost(src.url);
      copy.appendChild(title);
      copy.appendChild(host);
      link.appendChild(copy);
      item.appendChild(link);
      list.appendChild(item);
    });
    body.appendChild(list);
  }
  return body;
}
