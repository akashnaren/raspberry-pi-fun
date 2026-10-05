import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  CHART_UNAVAILABLE,
  chartBlock,
  chartFence,
  drawChart,
  figureFrom,
  parseChart,
} from "./src/chart.ts";
import { renderMarkdown } from "./src/markdown.ts";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

const NIGHT = {
  title: "Night",
  data: [{ type: "line", name: "Temp", x: ["Mon", "Tue"], y: [68, 71] }],
};

function hostFrom(html) {
  const match = String(html || "").match(/<script type="application\/json">([\s\S]*?)<\/script>/);
  return {
    querySelector() {
      return match ? { textContent: match[1] } : null;
    },
  };
}

function main() {
  const schema = JSON.parse(readFileSync(join(here, "chart.schema.json"), "utf8"));
  assert.deepEqual(schema.required, ["data"]);
  assert.equal(schema.properties.data.minItems, 1);
  assert.equal(schema.properties.data.maxItems, 6);
  assert.ok(schema.properties.data.items.required.includes("type"));
  assert.equal(schema.properties.data.items.properties.y.minItems, 1);
  assert.deepEqual(schema.properties.data.items.properties.type.enum, ["bar", "scatter", "line", "pie"]);
  assert.match(schema.description, /do not invent points/i);

  const spec = parseChart(JSON.stringify(NIGHT));
  assert.ok(spec);
  assert.deepEqual(spec.data[0].y, [68, 71]);
  assert.deepEqual(spec.data[0].x, ["Mon", "Tue"]);
  const figure = figureFrom(spec);
  assert.ok(figure);
  assert.deepEqual(figure.data[0].y, [68, 71]);
  assert.equal(figure.data[0].mode, "lines");
  assert.equal(figure.layout.title.text, "Night");
  assert.equal(figure.config.displayModeBar, false);
  assert.equal(JSON.stringify(figure).includes("plotly.js"), false);

  const html = renderMarkdown("Temps fell.\n\n```chart\n" + JSON.stringify(NIGHT) + "\n```\n\nStill cold.");
  assert.match(html, /Temps fell/);
  assert.match(html, /Still cold/);
  assert.match(html, /class="pi-chart"/);
  const embedded = html.match(/<script type="application\/json">([\s\S]*?)<\/script>/);
  assert.ok(embedded);
  assert.deepEqual(JSON.parse(embedded[1]).data[0].y, [68, 71]);

  let plotted = null;
  const drawn = drawChart(hostFrom(html), (_node, data, layout, config) => {
    plotted = { data, layout, config };
  });
  assert.equal(drawn, true);
  assert.deepEqual(plotted.data[0].y, [68, 71]);
  assert.deepEqual(plotted.data[0].x, ["Mon", "Tue"]);
  assert.equal(plotted.data.length, 1);
  assert.equal(plotted.config.displaylogo, false);

  const whole = renderMarkdown(JSON.stringify({ data: [{ type: "bar", y: [4, 9] }] }));
  assert.match(whole, /class="pi-chart"/);
  assert.deepEqual(JSON.parse(whole.match(/<script type="application\/json">([\s\S]*?)<\/script>/)[1]).data[0].y, [4, 9]);

  const emptyPayloads = [
    "{}",
    '{"data":[]}',
    '{"title":"Empty","data":[{"type":"bar","y":[]}]}',
    '{"data":[{"type":"line"}]}',
    '{"data":[{"type":"bar","y":["1","2"]}]}',
    '{"data":[{"type":"scatter","y":[1,2],"x":["only"]}]}',
    '{"data":[{"type":"heatmap","y":[1,2,3]}]}',
    "not json",
    "",
  ];
  for (const payload of emptyPayloads) {
    assert.equal(parseChart(payload), null, payload);
    assert.equal(chartBlock(payload), null, payload);
    let called = false;
    assert.equal(
      drawChart({ querySelector: () => ({ textContent: payload }) }, () => {
        called = true;
      }),
      false,
      payload,
    );
    assert.equal(called, false, payload);
  }

  const tooMany = {
    data: [1, 2, 3, 4, 5, 6, 7].map((item) => ({ type: "bar", y: [item] })),
  };
  assert.equal(parseChart(JSON.stringify(tooMany)), null);
  const longY = { data: [{ type: "line", y: Array.from({ length: 241 }, (_item, index) => index) }] };
  assert.equal(parseChart(JSON.stringify(longY)), null);

  const prose = renderMarkdown("Can you plot the weather?");
  assert.equal(prose.includes("pi-chart"), false);
  assert.equal(prose.includes("plotly"), false);

  const broken = renderMarkdown('```plotly\n{"title":"Nope","data":[{"type":"bar","y":[]}]}\n```');
  assert.equal(broken.includes("pi-chart"), false);
  assert.match(broken, /<pre><code>/);
  assert.match(broken, /Nope/);
  assert.equal(chartFence("image", JSON.stringify(NIGHT)), null);
  assert.equal(chartFence("gallery", JSON.stringify(NIGHT)), null);
  const image = renderMarkdown("```image\n{\"src\":\"https://example.com/a.png\"}\n```");
  assert.equal(image.includes("pi-chart"), false);

  const zeros = parseChart('{"data":[{"type":"scatter","y":[0,0]}]}');
  assert.deepEqual(zeros.data[0].y, [0, 0]);
  const pie = figureFrom(parseChart('{"data":[{"type":"pie","values":[1,-2]}]}'));
  assert.deepEqual(pie.data[0].values, [1, -2]);
  assert.equal(Object.hasOwn(pie.data[0], "y"), false);

  const raw = {
    data: [{ type: "bar", y: [2, 4], name: "Load", customdata: ["<img src=x>"] }],
    layout: { title: "Real", annotations: [{ text: "<script>alert(1)</script>" }] },
    config: { displayModeBar: true },
  };
  const kept = figureFrom(parseChart(JSON.stringify(raw)));
  assert.deepEqual(kept.data[0].y, [2, 4]);
  assert.equal(Object.hasOwn(kept.data[0], "customdata"), false);
  assert.equal(kept.layout.title.text, "Real");
  assert.equal(JSON.stringify(kept).includes("alert(1)"), false);
  assert.equal(JSON.stringify(kept).includes("<script>"), false);
  assert.equal(kept.config.displayModeBar, false);

  const hostile = parseChart('{"title":"<img>","layout":{"title":{"text":"Kept"}},"data":[{"type":"line","y":[3]}]}');
  assert.equal(hostile.title, "Kept");
  assert.equal(JSON.stringify(figureFrom(hostile)).includes("<img>"), false);

  const loose = renderMarkdown('```JSON\n{"data":[{"type":"Bar","y":[1, 2,],}]}\n```');
  assert.match(loose, /class="pi-chart"/);
  assert.deepEqual(JSON.parse(loose.match(/<script type="application\/json">([\s\S]*?)<\/script>/)[1]).data[0].y, [1, 2]);
  const config = renderMarkdown('```json\n{"host":"pi4"}\n```');
  assert.equal(config.includes("pi-chart"), false);

  const node = hostFrom(chartBlock(JSON.stringify({ data: [{ type: "bar", y: [1] }] })));
  node.isConnected = true;
  node.textContent = "";
  node.classList = { add(token) { node.token = token; } };
  assert.equal(drawChart(node, () => Promise.reject(new Error("plot failed"))), true);
  return Promise.resolve().then(() => {
    assert.equal(node.textContent, CHART_UNAVAILABLE);
    assert.equal(node.token, "pi-chart-failed");
    assert.equal(drawChart(hostFrom(chartBlock(JSON.stringify(NIGHT))), () => {
      throw new Error("sync");
    }), false);

    const shipped = readFileSync(join(here, "../static/plotly.min.js"));
    const official = readFileSync(join(dirname(require.resolve("plotly.js-basic-dist-min/package.json")), "plotly-basic.min.js"));
    assert.ok(shipped.equals(official));
    const header = shipped.subarray(0, 240).toString("utf8");
    assert.match(header, /plotly\.js/);
    assert.match(shipped.toString("utf8"), /newPlot/);
    const mesh = readFileSync(join(here, "../static/mesh.js"), "utf8");
    const client = readFileSync(join(here, "src/main.ts"), "utf8");
    const page = readFileSync(join(here, "../static/index.html"), "utf8");
    const build = readFileSync(join(here, "build.mjs"), "utf8");
    assert.match(mesh, /\/static\/plotly\.min\.js/);
    assert.match(client, /\/static\/plotly\.min\.js/);
    assert.match(build, /plotly\.js-basic-dist-min/);
    for (const host of ["cdn.plot.ly", "unpkg.com", "jsdelivr.net", "cdnjs.cloudflare.com"]) {
      assert.equal(mesh.includes(host), false, host);
      assert.equal(client.includes(host), false, host);
      assert.equal(page.includes(host), false, host);
    }
    console.log("ok");
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
