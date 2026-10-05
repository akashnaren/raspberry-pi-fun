/** A small flowchart TD renderer for ```mermaid fences. Other diagrams stay source. */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

interface FlowNode {
  id: string;
  label: string;
  decision: boolean;
}

interface FlowEdge {
  from: string;
  to: string;
  label: string;
}

function clip(text: string, limit: number): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > limit ? clean.slice(0, limit - 1) + "…" : clean;
}

export function parseFlow(source: string): { nodes: FlowNode[]; edges: FlowEdge[] } | null {
  const lines = String(source || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("%%"));
  if (!lines.length || !/^flowchart\s+(?:TD|TB|LR)\b/i.test(lines[0])) return null;
  const nodes = new Map<string, FlowNode>();
  const edges: FlowEdge[] = [];
  const remember = (id: string, label: string, decision: boolean) => {
    const key = id.trim();
    if (!/^[A-Za-z][\w]{0,16}$/.test(key)) return;
    const known = nodes.get(key);
    if (known && known.label !== key) return;
    nodes.set(key, { id: key, label: clip(label || key, 42), decision });
  };
  const nodeRef = "([A-Za-z][\\w]{0,16})(?:\\[([^\\]]+)\\]|\\(([^)]+)\\)|\\{([^}]+)\\})?";
  const edge = new RegExp(`^${nodeRef}\\s*-->\\s*(?:\\|([^|]+)\\|\\s*)?${nodeRef}\\s*$`);
  for (const line of lines.slice(1)) {
    const match = edge.exec(line);
    if (!match) continue;
    const fromLabel = match[2] || match[3] || match[4] || match[1];
    const toLabel = match[7] || match[8] || match[9] || match[6];
    remember(match[1], fromLabel, Boolean(match[4]));
    remember(match[6], toLabel, Boolean(match[9]));
    edges.push({ from: match[1], to: match[6], label: clip(match[5] || "", 24) });
  }
  if (nodes.size < 2 || !edges.length || nodes.size > 12) return null;
  return { nodes: [...nodes.values()], edges };
}

export function flowchartSvg(source: string): string | null {
  const flow = parseFlow(source);
  if (!flow) return null;
  const order: string[] = [];
  const seen = new Set<string>();
  const visit = (id: string) => {
    if (seen.has(id)) return;
    seen.add(id);
    order.push(id);
    flow.edges.filter((edge) => edge.from === id).forEach((edge) => visit(edge.to));
  };
  flow.nodes.forEach((node) => visit(node.id));
  const width = 280;
  const row = 72;
  const height = 28 + order.length * row;
  const byId = new Map(flow.nodes.map((node) => [node.id, node]));
  const yOf = new Map(order.map((id, index) => [id, 36 + index * row]));
  const edges = flow.edges
    .map((edge) => {
      const y1 = (yOf.get(edge.from) || 0) + 16;
      const y2 = (yOf.get(edge.to) || 0) - 18;
      if (!y1 || !y2) return "";
      const label = edge.label
        ? `<text x="${width / 2 + 8}" y="${(y1 + y2) / 2}" fill="currentColor" font-size="11">${escapeHtml(edge.label)}</text>`
        : "";
      return `<line x1="${width / 2}" y1="${y1}" x2="${width / 2}" y2="${y2}" stroke="currentColor" stroke-width="1.4"/>${label}`;
    })
    .join("");
  const boxes = order
    .map((id) => {
      const node = byId.get(id);
      if (!node) return "";
      const y = yOf.get(id) || 0;
      const label = escapeHtml(node.label);
      if (node.decision) {
        return `<polygon points="${width / 2},${y - 20} ${width / 2 + 78},${y} ${width / 2},${y + 20} ${width / 2 - 78},${y}" fill="none" stroke="currentColor" stroke-width="1.4"/><text x="${width / 2}" y="${y + 4}" text-anchor="middle" fill="currentColor" font-size="12">${label}</text>`;
      }
      return `<rect x="${width / 2 - 88}" y="${y - 16}" width="176" height="32" rx="8" fill="none" stroke="currentColor" stroke-width="1.4"/><text x="${width / 2}" y="${y + 4}" text-anchor="middle" fill="currentColor" font-size="12">${label}</text>`;
    })
    .join("");
  return `<svg class="flow-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Flowchart">${edges}${boxes}</svg>`;
}

function diagramPlaceholder(source: string): string {
  const payload = JSON.stringify({ source: source.replace(/\n$/, "") }).replace(/</g, "\\u003c");
  return `<div class="pi-diagram" role="img" aria-label="Flowchart"><script type="application/json">${payload}</script></div>`;
}

function diagramSource(raw: string): string {
  try {
    const parsed = JSON.parse(raw) as { source?: unknown };
    return typeof parsed.source === "string" ? parsed.source : "";
  } catch {
    return "";
  }
}

/** Leave a placeholder. The SVG is drawn later, and only when a flow node exists. */
export function mermaidFence(lang: string, code: string): string | null {
  if (lang.trim().toLowerCase() !== "mermaid") return null;
  if (parseFlow(code)) return diagramPlaceholder(code);
  const source = escapeHtml(code.replace(/\n$/, ""));
  return `<pre class="diagram-source"><code>${source}</code></pre>`;
}

export function mountDiagrams(root: ParentNode): void {
  const nodes = root.querySelectorAll(".pi-diagram");
  if (!nodes.length) return;
  nodes.forEach((node) => {
    const host = node as Element;
    if (host.querySelector("svg")) return;
    const script = host.querySelector('script[type="application/json"]');
    const svg = flowchartSvg(diagramSource(script?.textContent || ""));
    if (!svg || typeof host.insertAdjacentHTML !== "function") return;
    host.insertAdjacentHTML("beforeend", svg);
  });
}
