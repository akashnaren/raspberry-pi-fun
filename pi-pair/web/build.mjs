import * as esbuild from "esbuild";
import * as sass from "sass";
import fs from "fs";
import path from "path";
import { createRequire } from "module";
import { fileURLToPath } from "url";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const require = createRequire(import.meta.url);

const root = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(root, "../static");
fs.mkdirSync(outDir, { recursive: true });

const twSource = fs.readFileSync(path.join(root, "src/tailwind.css"), "utf8");
const tw = await postcss([
  tailwindcss({ config: path.join(root, "tailwind.config.js") }),
]).process(twSource, { from: undefined });

const compiled = sass.compile(path.join(root, "src/styles.scss"), {
  style: "compressed",
});
const katexRoot = path.dirname(require.resolve("katex/package.json"));
const fontSrc = path.join(katexRoot, "dist/fonts");
let katexCss = fs.readFileSync(path.join(katexRoot, "dist/katex.min.css"), "utf8");
const faces = [];
katexCss = katexCss.replace(/@font-face\{[^}]+\}/g, (block) => {
  let face = block.replace(/url\(fonts\/(KaTeX_[^)]+\.woff2)\)/g, (_all, file) => {
    const bytes = fs.readFileSync(path.join(fontSrc, file));
    return `url(data:font/woff2;base64,${bytes.toString("base64")})`;
  });
  face = face.replace(/,url\(fonts\/KaTeX_[^)]+\.(?:woff|ttf)\) format\("[^"]+"\)/g, "");
  faces.push(face);
  return "";
});
for (const name of fs.readdirSync(outDir)) {
  if (/^type-\d+\.css$/.test(name)) fs.unlinkSync(path.join(outDir, name));
}
const imports = [];
let bucket = "";
let part = 0;
const flushFaces = () => {
  if (!bucket) return;
  part += 1;
  const name = `type-${part}.css`;
  fs.writeFileSync(path.join(outDir, name), bucket);
  imports.push(`@import url("${name}");`);
  bucket = "";
};
for (const face of faces) {
  if (bucket && bucket.length + face.length > 48000) flushFaces();
  bucket += face;
}
flushFaces();
fs.copyFileSync(path.join(katexRoot, "LICENSE"), path.join(outDir, "katex-license.txt"));
const plotlyPkg = path.dirname(require.resolve("plotly.js-basic-dist-min/package.json"));
fs.copyFileSync(path.join(plotlyPkg, "plotly-basic.min.js"), path.join(outDir, "plotly.min.js"));
fs.copyFileSync(path.join(plotlyPkg, "LICENSE"), path.join(outDir, "plotly-license.txt"));
fs.writeFileSync(
  path.join(outDir, "mesh.css"),
  `${imports.join("")}\n${tw.css}\n${compiled.css}\n${katexCss}`,
);
fs.copyFileSync(path.join(root, "index.html"), path.join(outDir, "index.html"));
for (const name of ["favicon.svg", "favicon-32.png", "apple-touch-icon.png"]) {
  const src = path.join(root, name);
  if (fs.existsSync(src)) fs.copyFileSync(src, path.join(outDir, name));
}

await esbuild.build({
  entryPoints: [path.join(root, "src/main.ts")],
  bundle: true,
  outfile: path.join(outDir, "mesh.js"),
  target: "es2020",
  minify: true,
  legalComments: "none",
  format: "iife",
});
