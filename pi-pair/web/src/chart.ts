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

export function mountCharts(root: ParentNode | null): void {
  if (!root || typeof document === "undefined") return;
  const nodes = [...root.querySelectorAll(".pi-chart")];
  if (!nodes.length) return;
  const ready: Element[] = [];
  const pending: Element[] = [];
  for (const node of nodes) {
    if (node.getAttribute("data-drawn") === "1") continue;
    const script = node.querySelector("script");
    if (!script) continue;
    try {
      const spec = JSON.parse(script.textContent || "") as Figure & { plot?: string };
      if (Array.isArray(spec.data)) ready.push(node);
      else if (spec.plot) pending.push(node);
    } catch {
      /* the table or the expression stays visible */
    }
  }
  if (!ready.length && !pending.length) return;
  loadPlotly()
    .then(() => {
      for (const node of ready) {
        const script = node.querySelector("script");
        if (!script) continue;
        draw(node, JSON.parse(script.textContent || "") as Figure);
      }
      for (const node of pending) {
        const script = node.querySelector("script");
        if (!script) continue;
        const spec = JSON.parse(script.textContent || "") as { plot?: string };
        fetch("/tools/render_chart", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ plot: spec.plot || "" }),
        })
          .then((response) => (response.ok ? response.json() : null))
          .then((body: { figure?: Figure } | null) => {
            if (body?.figure) draw(node, body.figure);
          })
          .catch(() => {
            /* the fenced expression stays visible */
          });
      }
    })
    .catch(() => {
      /* the table or the expression stays visible */
    });
}
