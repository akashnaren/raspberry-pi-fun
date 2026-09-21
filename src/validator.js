import { cp, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const PRODUCT_INDEX = "product/index.html";

export function syntaxCheckJs(src) {
  const text = String(src);
  if (text.includes("```")) {
    return { ok: false, error: "markdown fence in script" };
  }
  const stack = [];
  const pairs = { "(": ")", "[": "]", "{": "}" };
  let quote = null;
  let escape = false;
  let lineComment = false;
  let blockComment = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    const next = text[i + 1];
    if (lineComment) {
      if (ch === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (ch === "*" && next === "/") {
        blockComment = false;
        i += 1;
      }
      continue;
    }
    if (quote) {
      if (escape) {
        escape = false;
        continue;
      }
      if (ch === "\\") {
        escape = true;
        continue;
      }
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === "/" && next === "/") {
      lineComment = true;
      i += 1;
      continue;
    }
    if (ch === "/" && next === "*") {
      blockComment = true;
      i += 1;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      continue;
    }
    if (pairs[ch]) {
      stack.push(pairs[ch]);
      continue;
    }
    if (ch === ")" || ch === "]" || ch === "}") {
      if (stack.pop() !== ch) return { ok: false, error: `unbalanced ${ch}` };
    }
  }
  if (quote) return { ok: false, error: "unterminated string" };
  if (blockComment) return { ok: false, error: "unterminated comment" };
  if (stack.length) return { ok: false, error: "unbalanced brackets" };
  return { ok: true };
}

export function syntaxCheckHtml(html) {
  const text = String(html);
  if (text.trim().length < 20) return { ok: false, error: "html too small" };
  if (!/<html[\s>]/i.test(text) && !/<!doctype/i.test(text)) {
    return { ok: false, error: "missing html document" };
  }
  if (/<parsererror/i.test(text)) return { ok: false, error: "parser error marker" };
  const openScripts = (text.match(/<script\b/gi) || []).length;
  const closeScripts = (text.match(/<\/script>/gi) || []).length;
  if (openScripts !== closeScripts) return { ok: false, error: "unclosed script tag" };
  for (const match of text.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) {
    const src = match[1];
    if (!src.trim()) continue;
    const js = syntaxCheckJs(src);
    if (!js.ok) return js;
  }
  return { ok: true };
}

export function syntaxCheckFile(rel, contents) {
  if (rel.endsWith(".html") || rel.endsWith(".htm")) return syntaxCheckHtml(contents);
  if (rel.endsWith(".js")) return syntaxCheckJs(contents);
  if (rel.endsWith(".json")) {
    try {
      JSON.parse(contents);
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }
  if (!String(contents).trim()) return { ok: false, error: "empty file" };
  return { ok: true };
}

export function createPromoter({ workspaceRoot, distRoot }) {
  return {
    async currentIndex() {
      try {
        return await readFile(join(distRoot, "index.html"), "utf8");
      } catch {
        return null;
      }
    },
    async promoteProduct() {
      await mkdir(distRoot, { recursive: true });
      const productDir = join(workspaceRoot, "product");
      const staging = `${distRoot}.staging`;
      await rm(staging, { recursive: true, force: true });
      await cp(productDir, staging, { recursive: true });
      await rm(distRoot, { recursive: true, force: true });
      await cp(staging, distRoot, { recursive: true });
      await rm(staging, { recursive: true, force: true });
    },
    async seedDistIfMissing() {
      try {
        await readFile(join(distRoot, "index.html"), "utf8");
        return false;
      } catch {
        const source = await readFile(join(workspaceRoot, PRODUCT_INDEX), "utf8");
        const check = syntaxCheckHtml(source);
        if (!check.ok) throw new Error(`seed product is not green: ${check.error}`);
        await this.promoteProduct();
        return true;
      }
    },
    async listDist() {
      try {
        return await readdir(distRoot);
      } catch {
        return [];
      }
    },
  };
}

export async function writeFileAtomic(abs, contents) {
  await mkdir(dirname(abs), { recursive: true });
  await writeFile(abs, contents, "utf8");
}
