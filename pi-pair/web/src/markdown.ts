import katex from "katex";
import { plotBlock, tableChart } from "./chart.ts";
import { mermaidFence } from "./diagram.ts";
import { docCard } from "./doc.ts";
import { isTableRule, markdownTable } from "./table.ts";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderTex(source: string, display: boolean): string {
  try {
    return katex.renderToString(source.trim(), {
      displayMode: display,
      throwOnError: false,
      strict: "ignore",
      output: "html",
      trust: false,
    });
  } catch {
    return `<code>${escapeHtml(source)}</code>`;
  }
}

function isInlineTex(body: string): boolean {
  const tex = body.trim();
  if (!tex || tex.length > 120) return false;
  if (/^\d/.test(tex) && !/[\\^=_]/.test(tex)) return false;
  if (!/[a-zA-Z\\^_=]/.test(tex)) return false;
  return true;
}

function inline(text: string): string {
  const codes: string[] = [];
  const maths: string[] = [];
  let work = text.replace(/`([^`]+)`/g, (_all, code: string) => {
    const token = `\u0000C${codes.length}\u0000`;
    codes.push(`<code>${escapeHtml(code)}</code>`);
    return token;
  });
  work = work.replace(/\\\(([\s\S]*?)\\\)/g, (_all, tex: string) => {
    const token = `\u0000M${maths.length}\u0000`;
    maths.push(renderTex(tex, false));
    return token;
  });
  work = work.replace(/(^|[^\\$])\$(?!\$)([^$\n]+?)\$(?!\$)/g, (all, pre: string, body: string) => {
    if (!isInlineTex(body)) return all;
    const token = `\u0000M${maths.length}\u0000`;
    maths.push(renderTex(body, false));
    return pre + token;
  });
  let html = escapeHtml(work);
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^\*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // NUL sentinels keep fenced code and math out of the HTML pass.
  // biome-ignore lint/suspicious/noControlCharactersInRegex: placeholder bytes are intentional
  html = html.replace(/\u0000C(\d+)\u0000/g, (_all, index: string) => codes[Number(index)] ?? "");
  // biome-ignore lint/suspicious/noControlCharactersInRegex: placeholder bytes are intentional
  html = html.replace(/\u0000M(\d+)\u0000/g, (_all, index: string) => maths[Number(index)] ?? "");
  return html;
}

const FILE_LANG = new Set(["doc", "pdf", "docx", "xlsx", "txt", "csv"]);

function docSource(lang: string, code: string): string {
  const kind = lang.toLowerCase();
  if (kind !== "doc" && FILE_LANG.has(kind) && !/^kind\s*:/im.test(code)) {
    return `kind: ${kind}\n${code}`;
  }
  return code;
}

/** A reply that is only a ```markdown or ```md fence. Inner GFM should render. */
function soleMarkdown(text: string): { body: string } | null {
  const match = /^```(?:markdown|md)[ \t]*\n([\s\S]*?)\n?```$/.exec(text.trim());
  if (!match) return null;
  return { body: match[1] };
}

export function renderMarkdown(source: string): string {
  const original = String(source ?? "").replace(/\r\n/g, "\n");
  const sole = soleMarkdown(original);
  const text = sole ? sole.body : original;
  const blocks: string[] = [];
  const seenDocs = new Set<string>();
  const stash = (html: string) => {
    const token = `@@BLOCK${blocks.length}@@`;
    blocks.push(html);
    return token;
  };
  const stashDoc = (code: string) => {
    const card = docCard(code);
    const key = card.replace(/\s+/g, " ");
    if (seenDocs.has(key)) return stash("");
    seenDocs.add(key);
    return stash(card);
  };
  const fenced = text.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_all, lang: string, code: string) => {
    const label = String(lang || "").toLowerCase();
    const body = String(code || "");
    if (!body.trim()) return "";
    const flow = mermaidFence(label, body);
    if (flow) return stash(flow);
    if (label === "plot" || (label === "chart" && body.includes("|") && /-{3,}/.test(body))) {
      return stash(plotBlock(body));
    }
    if (FILE_LANG.has(label)) return stashDoc(docSource(label, body));
    const token = /^[A-Za-z0-9_+-]{1,16}$/.test(label) ? label : "";
    const klass = token ? ` class="language-${token}"` : "";
    return stash(`<pre><code${klass}>${escapeHtml(body.replace(/\n$/, ""))}</code></pre>`);
  });
  const withDisplay = fenced
    .replace(/\\\[([\s\S]*?)\\\]/g, (_all, tex: string) => stash(renderTex(tex, true)))
    .replace(/\$\$([\s\S]*?)\$\$/g, (_all, tex: string) => stash(renderTex(tex, true)));
  const lines = withDisplay.split("\n");
  const out: string[] = [];
  let list: "ul" | "ol" | "" = "";
  const closeList = () => {
    if (list) {
      out.push(list === "ul" ? "</ul>" : "</ol>");
      list = "";
    }
  };
  const flushParagraph = (buf: string[]) => {
    if (!buf.length) return;
    const body = buf.map((line) => inline(line)).join("<br>");
    out.push(`<p>${body}</p>`);
    buf.length = 0;
  };
  const paragraph: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.includes("|") && i + 1 < lines.length && isTableRule(lines[i + 1])) {
      const body: string[] = [];
      let j = i + 2;
      while (j < lines.length && lines[j].includes("|") && lines[j].trim()) {
        body.push(lines[j]);
        j += 1;
      }
      const table = markdownTable(line, body);
      if (table) {
        flushParagraph(paragraph);
        closeList();
        out.push(stash(table + tableChart(line, body)));
        i = j - 1;
        continue;
      }
    }
    if (/^@@BLOCK\d+@@$/.test(line.trim())) {
      flushParagraph(paragraph);
      closeList();
      out.push(line.trim());
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph(paragraph);
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^---+$/.test(line.trim())) {
      flushParagraph(paragraph);
      closeList();
      out.push("<hr>");
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      flushParagraph(paragraph);
      closeList();
      out.push(`<blockquote><p>${inline(quote[1])}</p></blockquote>`);
      continue;
    }
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    const ordered = /^\d+\.\s+(.*)$/.exec(line);
    if (bullet || ordered) {
      flushParagraph(paragraph);
      const kind = bullet ? "ul" : "ol";
      if (list !== kind) {
        closeList();
        list = kind;
        out.push(kind === "ul" ? "<ul>" : "<ol>");
      }
      out.push(`<li>${inline((bullet || ordered)![1])}</li>`);
      continue;
    }
    if (!line.trim()) {
      flushParagraph(paragraph);
      closeList();
      continue;
    }
    closeList();
    paragraph.push(line);
  }
  flushParagraph(paragraph);
  closeList();
  let html = out
    .join("\n")
    .replace(/@@BLOCK(\d+)@@/g, (_all, index: string) => blocks[Number(index)] ?? "");
  if (sole && sole.body.trim()) {
    html = `${stashDoc(`kind: md\ntitle: note\n${sole.body}`)}\n${html}`.replace(
      /@@BLOCK(\d+)@@/g,
      (_all, index: string) => blocks[Number(index)] ?? "",
    );
  }
  return html;
}

function oddMarker(text: string, marker: string): boolean {
  return text.split(marker).length % 2 === 0;
}

/** Close a fence or display-math marker that the stream has not finished. */
export function stabilizeMarkdown(source: string): string {
  let text = String(source ?? "");
  if (oddMarker(text, "```")) text += "\n```";
  if (oddMarker(text, "$$")) text += "$$";
  const openDisp = (text.match(/\\\[/g) || []).length;
  const closeDisp = (text.match(/\\\]/g) || []).length;
  if (openDisp > closeDisp) text += "\\]";
  const openInline = (text.match(/\\\(/g) || []).length;
  const closeInline = (text.match(/\\\)/g) || []).length;
  if (openInline > closeInline) text += "\\)";
  return closeDanglingDollar(text);
}

function closeDanglingDollar(text: string): string {
  const masked = text.replace(/\$\$[\s\S]*?\$\$/g, (block) => " ".repeat(block.length));
  let open = -1;
  for (let i = 0; i < masked.length; i += 1) {
    if (masked[i] !== "$") continue;
    if (masked[i + 1] === "$" || (i > 0 && masked[i - 1] === "$")) continue;
    open = open < 0 ? i : -1;
  }
  if (open < 0) return text;
  const body = text.slice(open + 1);
  if (!body || isInlineTex(body)) return text + "$";
  return text;
}

export function renderStreamingMarkdown(source: string): string {
  return renderMarkdown(stabilizeMarkdown(source));
}
