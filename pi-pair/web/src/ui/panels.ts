import { scrubAssistant } from "../chat/copy";
import { dropStrayMarkers, linkCitations, renderFailedSearch, renderSourcesPanelBody, renderSourcesPill, type PanelDetail, type SourceLink as PillSource } from "../chat/sources";
import { thoughtSummary, withoutThinkTags } from "../chat/text";
import { byId, el } from "../core/dom";
import type { SourceLink, StageName } from "../core/types";
import { mountCharts } from "../render/chart";
import { mountDiagrams } from "../render/diagram";
import { mountDocs } from "../render/doc";
import { renderMarkdown, renderStreamingMarkdown } from "../render/markdown";

export function thoughtPanel(text: string, seconds: number, live: boolean): HTMLDetailsElement {
  const box = document.createElement("details");
  box.className = "thought";
  if (live) box.open = true;
  const summary = document.createElement("summary");
  summary.textContent = thoughtSummary(seconds, live);
  const body = el("div", "thought-body");
  body.textContent = text;
  box.append(summary, body);
  return box;
}

export function hideEmpty(): void {
  document.getElementById("empty")?.remove();
}

export function setSettingsOpen(on: boolean): void {
  byId("ioPanel").classList.toggle("open", on);
  if (on) byId("sourcesPanel").classList.remove("open");
  const sourcesOpen = byId("sourcesPanel").classList.contains("open");
  byId("overlay").classList.toggle("open", on || sourcesOpen);
}

export function setBodyContent(
  node: HTMLElement,
  text: string,
  asMd: boolean,
  streaming = false,
  sourceCount = 0,
): void {
  const shown = dropStrayMarkers(scrubAssistant(withoutThinkTags(text)), sourceCount, !streaming);
  if (asMd) {
    node.classList.add("md");
    const html = streaming ? renderStreamingMarkdown(shown) : renderMarkdown(shown);
    node.innerHTML = streaming ? html : linkCitations(html, sourceCount);
    mountDiagrams(node);
    if (!streaming) {
      mountCharts(node);
      mountDocs(node);
    }
  } else {
    node.classList.remove("md");
    node.textContent = shown;
  }
}

export function setSourcesOpen(on: boolean): void {
  const panel = byId("sourcesPanel");
  panel.classList.toggle("open", on);
  panel.setAttribute("aria-hidden", on ? "false" : "true");
  const settingsOpen = byId("ioPanel").classList.contains("open");
  byId("overlay").classList.toggle("open", on || settingsOpen);
}

export function openSourcesPanel(detail: PanelDetail): void {
  const body = byId("sourcesBody");
  body.replaceChildren(renderSourcesPanelBody(document, detail));
  setSettingsOpen(false);
  setSourcesOpen(true);
  document.querySelectorAll(".sources-pill").forEach((pill) => {
    pill.setAttribute("aria-expanded", "false");
  });
}

export function showSearch(parent: HTMLElement, status: string, sources: SourceLink[], stages: StageName[], prompt: string): void {
  if (status === "failed") {
    parent.appendChild(renderFailedSearch(document));
    return;
  }
  if (status !== "ok") return;
  const links = (sources || []).filter((src) => src && String(src.url || "").startsWith("http"));
  if (!links.length) return;
  const pill = renderSourcesPill(document, links as PillSource[]);
  const detail: PanelDetail = { status, sources: links, stages, prompt };
  pill.onclick = () => {
    const opening = !byId("sourcesPanel").classList.contains("open");
    if (!opening) {
      setSourcesOpen(false);
      pill.setAttribute("aria-expanded", "false");
      return;
    }
    openSourcesPanel(detail);
    pill.setAttribute("aria-expanded", "true");
  };
  parent.appendChild(pill);
}

export function thumbIcon(): SVGSVGElement {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.6");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M8 11v8a1 1 0 0 0 1 1h7.2a2 2 0 0 0 1.9-1.4l1.3-5.2A2 2 0 0 0 17.5 11H14V7.2A2.2 2.2 0 0 0 11.8 5c-.5 0-.9.3-1.1.7L8 11zM8 11H5.5A1.5 1.5 0 0 0 4 12.5v5A1.5 1.5 0 0 0 5.5 19H8");
  svg.appendChild(path);
  return svg;
}

export function motionReduced(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
