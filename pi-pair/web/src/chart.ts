/**
 * A reply chart. Draw it only when structured JSON already has numeric points.
 * Missing or empty data stays text. This module does not fill in a sample series.
 */

export const CHART_UNAVAILABLE = "Chart could not be drawn.";

const CHART_LANG = new Set(["chart", "plotly"]);
const CHART_TYPES = new Set(["bar", "scatter", "line", "pie"]);
const SCATTER_MODES = new Set(["lines", "markers", "lines+markers"]);
const MAX_SERIES = 6;
const MAX_POINTS = 240;
const MAX_TITLE = 120;
const MAX_LABEL = 80;

export type ChartType = "bar" | "scatter" | "line" | "pie";
export type ScatterMode = "lines" | "markers" | "lines+markers";

export interface ChartSeries {
  type: ChartType;
  y: number[];
  x?: (string | number)[];
  name?: string;
  mode?: ScatterMode;
}

export interface ChartSpec {
  title?: string;
  data: ChartSeries[];
}

export interface PlotlyFigure {
  data: Record<string, unknown>[];
  layout: Record<string, unknown>;
  config: Record<string, unknown>;
}

export interface ChartNode {
  querySelector(selector: string): { textContent: string | null } | null;
  isConnected?: boolean;
  classList?: { add(token: string): void };
  textContent?: string | null;
}

export type PlotFn = (
  node: HTMLElement,
  data: Record<string, unknown>[],
  layout: Record<string, unknown>,
  config: Record<string, unknown>,
) => void | Promise<unknown>;

const PLOT_CONFIG = {
  responsive: true,
  displayModeBar: false,
  displaylogo: false,
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function plain(value: unknown, max: number): string | undefined {
  let text: string | undefined;
  if (typeof value === "string") text = value;
  else {
    const record = asRecord(value);
    if (record && typeof record.text === "string") text = record.text;
  }
  if (text == null) return undefined;
  const trimmed = text.trim();
  if (!trimmed || trimmed.length > max || /[<>]/.test(trimmed)) return undefined;
  return trimmed;
}

function numbers(value: unknown): number[] | null {
  if (value == null) return null;
  if (!Array.isArray(value) || value.length < 1 || value.length > MAX_POINTS) return null;
  const out: number[] = [];
  for (const item of value) {
    if (typeof item !== "number" || !Number.isFinite(item)) return null;
    out.push(item);
  }
  return out;
}

function categories(value: unknown, count: number): (string | number)[] | null {
  if (value == null) return [];
  if (!Array.isArray(value) || value.length !== count) return null;
  const out: (string | number)[] = [];
  for (const item of value) {
    if (typeof item === "number") {
      if (!Number.isFinite(item)) return null;
      out.push(item);
    } else if (typeof item === "string") {
      const trimmed = item.trim();
      if (!trimmed || trimmed.length > MAX_LABEL || /[<>]/.test(trimmed)) return null;
      out.push(trimmed);
    } else {
      return null;
    }
  }
  return out;
}

function sameNumbers(left: number[], right: number[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function seriesFrom(item: unknown): ChartSeries | null {
  const row = asRecord(item);
  if (!row || typeof row.type !== "string" || !CHART_TYPES.has(row.type)) return null;
  const y = numbers(row.y);
  const values = numbers(row.values);
  let points: number[] | null = null;
  if (y && values) {
    if (!sameNumbers(y, values)) return null;
    points = y;
  } else {
    points = y || values;
  }
  if (!points) return null;
  const labels = categories(row.x != null ? row.x : row.labels, points.length);
  if (!labels) return null;
  const series: ChartSeries = { type: row.type as ChartType, y: points };
  const name = plain(row.name, MAX_LABEL);
  if (name) series.name = name;
  if (labels.length) series.x = labels;
  if (series.type === "scatter" && typeof row.mode === "string" && SCATTER_MODES.has(row.mode)) {
    series.mode = row.mode as ScatterMode;
  }
  return series;
}

function titleFrom(root: Record<string, unknown>): string | undefined {
  const direct = plain(root.title, MAX_TITLE);
  if (direct) return direct;
  const layout = asRecord(root.layout);
  if (!layout) return undefined;
  return plain(layout.title, MAX_TITLE);
}

export function parseChart(source: string): ChartSpec | null {
  const text = String(source ?? "").trim();
  if (!text) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  const root = asRecord(raw);
  if (!root || !Array.isArray(root.data)) return null;
  if (root.data.length < 1 || root.data.length > MAX_SERIES) return null;
  const data: ChartSeries[] = [];
  for (const item of root.data) {
    const series = seriesFrom(item);
    if (!series) return null;
    data.push(series);
  }
  const title = titleFrom(root);
  return title ? { title, data } : { data };
}

export function figureFrom(spec: ChartSpec): PlotlyFigure | null {
  if (!spec?.data?.length) return null;
  const data = spec.data.map((series) => {
    if (series.type === "pie") {
      const trace: Record<string, unknown> = { type: "pie", values: series.y.slice() };
      if (series.name) trace.name = series.name;
      if (series.x) trace.labels = series.x.slice();
      return trace;
    }
    const trace: Record<string, unknown> = {
      type: series.type === "bar" ? "bar" : "scatter",
      y: series.y.slice(),
    };
    if (series.type === "line") trace.mode = "lines";
    if (series.type === "scatter") trace.mode = series.mode || "markers";
    if (series.name) trace.name = series.name;
    if (series.x) trace.x = series.x.slice();
    return trace;
  });
  const showlegend = spec.data.length > 1;
  const layout: Record<string, unknown> = {
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    font: { color: "#ececec", family: "system-ui, sans-serif" },
    margin: { t: spec.title ? 48 : 20, r: 16, b: 44, l: 52 },
    height: 280,
    autosize: true,
    xaxis: { automargin: true, gridcolor: "#343434", zerolinecolor: "#454545" },
    yaxis: { automargin: true, gridcolor: "#343434", zerolinecolor: "#454545" },
    showlegend,
  };
  if (showlegend) {
    layout.legend = { orientation: "h", y: 1.08, x: 0, yanchor: "bottom" };
  }
  if (spec.title) layout.title = { text: spec.title };
  return { data, layout, config: { ...PLOT_CONFIG } };
}

function escapeAttr(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function chartBlock(source: string): string | null {
  const spec = parseChart(source);
  if (!spec) return null;
  const payload = JSON.stringify(spec).replace(/</g, "\\u003c");
  const label = escapeAttr(spec.title || "Chart");
  return `<div class="pi-chart" role="img" aria-label="${label}"><script type="application/json">${payload}</script></div>`;
}

export function chartFence(lang: string, code: string): string | null {
  if (!CHART_LANG.has(String(lang || "").trim().toLowerCase())) return null;
  return chartBlock(code);
}

export function readChart(node: ChartNode): PlotlyFigure | null {
  const script = node.querySelector('script[type="application/json"]');
  if (!script?.textContent) return null;
  const spec = parseChart(script.textContent);
  if (!spec) return null;
  return figureFrom(spec);
}

function isPromise(value: unknown): value is Promise<unknown> {
  return !!value && typeof (value as Promise<unknown>).then === "function";
}

export function failChart(node: ChartNode): void {
  if (node.isConnected === false) return;
  node.classList?.add("pi-chart-failed");
  node.textContent = CHART_UNAVAILABLE;
}

export function drawChart(node: ChartNode, plot: PlotFn): boolean {
  const figure = readChart(node);
  if (!figure) return false;
  try {
    const pending = plot(node as HTMLElement, figure.data, figure.layout, figure.config);
    if (isPromise(pending)) void pending.catch(() => failChart(node));
    return true;
  } catch {
    return false;
  }
}
