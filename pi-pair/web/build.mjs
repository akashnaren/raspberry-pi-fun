import * as esbuild from "esbuild";
import * as sass from "sass";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

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
fs.writeFileSync(path.join(outDir, "mesh.css"), `${tw.css}\n${compiled.css}`);
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
