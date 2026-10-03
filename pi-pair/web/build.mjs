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
const katexCss = fs.readFileSync(path.join(katexRoot, "dist/katex.min.css"), "utf8");
const fontSrc = path.join(katexRoot, "dist/fonts");
const fontDest = path.join(outDir, "fonts");
fs.mkdirSync(fontDest, { recursive: true });
for (const name of fs.readdirSync(fontSrc)) {
  fs.copyFileSync(path.join(fontSrc, name), path.join(fontDest, name));
}
fs.copyFileSync(path.join(katexRoot, "LICENSE"), path.join(fontDest, "LICENSE"));
fs.writeFileSync(path.join(outDir, "mesh.css"), `${tw.css}\n${compiled.css}\n${katexCss}`);
fs.copyFileSync(path.join(root, "index.html"), path.join(outDir, "index.html"));

await esbuild.build({
  entryPoints: [path.join(root, "src/main.ts")],
  bundle: true,
  outfile: path.join(outDir, "mesh.js"),
  target: "es2020",
  minify: true,
  legalComments: "none",
  format: "iife",
});
