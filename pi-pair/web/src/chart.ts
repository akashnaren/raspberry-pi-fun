/** Table shape to a Plotly figure. Mirrors pair/charts.py. Plotly loads only when a chart is on the page. */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const NUMBER = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;

export interface Figure {
  data: Array<Record<string, unknown>>;
  layout: Record<string, unknown>;
}

function split(line: string): string[] {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((part) => part.trim());
}

function isDate(cell: string): boolean {
  return DATE.test(cell.trim());
}

function isNumber(cell: string): boolean {
  const text = cell.trim().replace(/,/g, "");
  return Boolean(text) && !isDate(text) && NUMBER.test(text);
}

function asFloat(cell: string): number {
  return Number(cell.trim().replace(/,/g, ""));
}

function columnKind(cells: string[]): string {
  const filled = cells.filter((cell) => cell);
  if (!filled.length) return "empty";
  if (filled.every(isDate)) return "date";
  if (filled.every(isNumber)) return "number";
  return "text";
}

function ordered(cells: string[]): boolean {
  if (cells.length < 2 || cells.some((cell) => !isNumber(cell))) return false;
  const nums = cells.map(asFloat);
  const up = [...nums].sort((a, b) => a - b);
  const down = [...up].reverse();
  const same = (other: number[]) => nums.every((value, index) => value === other[index]);
  return same(up) || same(down);
}

export function chartType(headers: string[], rows: string[][]): string {
  if (headers.length < 2 || !rows.length) return "";
  const kinds = headers.map((_header, index) => columnKind(rows.map((row) => (row[index] ?? "").trim())));
  const first = rows.map((row) => (row[0] ?? "").trim());
  if (kinds[0] === "date" || (kinds[0] === "number" && ordered(first))) {
    if (kinds.slice(1).some((kind) => kind === "number")) return "line";
  }
  if (kinds[0] === "number" && kinds.filter((kind) => kind === "number").length >= 2 && !kinds.includes("text") && !kinds.includes("date")) {
    return "scatter";
  }
  if (kinds.includes("number") && kinds.includes("text")) return "bar";
  if (kinds.slice(1).some((kind) => kind === "number")) return "bar";
  return "";
}

function series(kind: string, headers: string[], rows: string[][]): Array<Record<string, unknown>> {
  const labels = rows.map((row) => row[0] ?? "");
  if (kind === "scatter") {
    return [
      {
        type: "scatter",
        mode: "markers",
        name: headers[1],
        x: rows.map((row) => asFloat(row[0] ?? "")),
        y: rows.map((row) => asFloat(row[1] ?? "")),
      },
    ];
  }
  const data: Array<Record<string, unknown>> = [];
  for (let index = 1; index < headers.length; index += 1) {
    const cells = rows.map((row) => row[index] ?? "");
    if (!cells.length || cells.some((cell) => cell.trim() && !isNumber(cell)) || cells.some((cell) => !cell.trim())) {
      continue;
    }
    const item: Record<string, unknown> = {
      name: headers[index],
      x: labels,
      y: cells.map(asFloat),
    };
    if (kind === "line") {
      item.type = "scatter";
      item.mode = "lines";
    } else {
      item.type = "bar";
    }
    data.push(item);
  }
  return data;
}

export function figureForTable(headers: string[], rows: string[][]): Figure | null {
  const kind = chartType(headers, rows);
  if (!kind) return null;
  const data = series(kind, headers, rows);
  if (!data.length) return null;
  return { data, layout: { margin: { t: 48, r: 16, b: 48, l: 48 } } };
}

function figureHtml(figure: Figure): string {
  const payload = JSON.stringify(figure).replace(/</g, "\\u003c");
  return `<div class="pi-chart"><script type="application/json">${payload}</script></div>`;
}

export function tableChart(header: string, body: string[]): string {
  const headers = split(header);
  const rows = body.map(split);
  if (headers.length < 2 || headers.some((item) => !item)) return "";
  const figure = figureForTable(headers, rows);
  return figure ? figureHtml(figure) : "";
}

export function plotBlock(source: string): string {
  const text = String(source ?? "").replace(/\n$/, "");
  const payload = JSON.stringify({ plot: text }).replace(/</g, "\\u003c");
  const shown = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return `<div class="pi-plot"><div class="pi-chart" data-plot="1"><script type="application/json">${payload}</script></div><pre class="pi-plot-src"><code>${shown}</code></pre></div>`;
}

interface PlotlyApi {
  newPlot: (node: Element, data: unknown, layout: unknown, config: Record<string, unknown>) => void;
}

function plotlyApi(): PlotlyApi | null {
  const host = window as Window & { Plotly?: PlotlyApi };
  return host.Plotly ?? null;
}

let loading: Promise<void> | null = null;

function loadPlotly(): Promise<void> {
  if (plotlyApi()) return Promise.resolve();
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/plotly.min.js";
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("plotly"));
      document.head.appendChild(script);
    });
  }
  return loading;
}

function draw(node: Element, figure: Figure): void {
  const api = plotlyApi();
  if (!api || node.getAttribute("data-drawn") === "1") return;
  node.setAttribute("data-drawn", "1");
  api.newPlot(node, figure.data, figure.layout, { displayModeBar: false, responsive: true });
}

const figures = new Map<string, Figure | null>();
const inflight = new Map<string, Promise<Figure | null>>();

function readSpec(node: Element): (Figure & { plot?: string }) | null {
  const script = node.querySelector("script");
  if (!script) return null;
  try {
    return JSON.parse(script.textContent || "") as Figure & { plot?: string };
  } catch {
    return null;
  }
}

function figureForPlot(plot: string): Promise<Figure | null> {
  if (figures.has(plot)) return Promise.resolve(figures.get(plot) ?? null);
  const pending = inflight.get(plot);
  if (pending) return pending;
  const job = fetch("/tools/render_chart", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ plot }),
  })
    .then((response) => (response.ok ? response.json() : null))
    .then((body: { figure?: Figure } | null) => {
      const figure = body?.figure && Array.isArray(body.figure.data) ? body.figure : null;
      figures.set(plot, figure);
      return figure;
    })
    .catch(() => {
      figures.set(plot, null);
      return null;
    })
    .finally(() => {
      inflight.delete(plot);
    });
  inflight.set(plot, job);
  return job;
}

function revealChart(node: Element): void {
  if (node.getAttribute("data-drawn") === "1") return;
  const spec = readSpec(node);
  if (!spec) return;
  if (Array.isArray(spec.data)) {
    draw(node, spec);
    return;
  }
  if (!spec.plot) return;
  const plot = spec.plot;
  if (figures.has(plot)) {
    const cached = figures.get(plot);
    if (cached) draw(node, cached);
    return;
  }
  void figureForPlot(plot).then((figure) => {
    if (figure) draw(node, figure);
  });
}

export function mountCharts(root: ParentNode | null): void {
  if (!root || typeof document === "undefined") return;
  const nodes = [...root.querySelectorAll(".pi-chart")].filter((node) => node.getAttribute("data-drawn") !== "1");
  if (!nodes.length) return;
  const paint = () => {
    for (const node of nodes) revealChart(node);
  };
  if (typeof IntersectionObserver === "function") {
    const io = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting);
        if (!visible.length) return;
        for (const entry of visible) io.unobserve(entry.target);
        loadPlotly()
          .then(() => {
            for (const entry of visible) revealChart(entry.target);
          })
          .catch(() => {
            /* the table or the expression stays visible */
          });
      },
      { rootMargin: "200px" },
    );
    for (const node of nodes) io.observe(node);
    return;
  }
  loadPlotly()
    .then(paint)
    .catch(() => {
      /* the table or the expression stays visible */
    });
}
