import katex from "katex";
import { chartBlock, chartFence } from "./chart.ts";

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
  let html = escapeHtml(work);
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^\*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  html = html.replace(/\u0000C(\d+)\u0000/g, (_all, index: string) => codes[Number(index)] ?? "");
  html = html.replace(/\u0000M(\d+)\u0000/g, (_all, index: string) => maths[Number(index)] ?? "");
  return html;
}

export function renderMarkdown(source: string): string {
  const text = String(source ?? "").replace(/\r\n/g, "\n");
  const trimmed = text.trim();
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    const only = chartBlock(trimmed);
    if (only) return only;
  }
  const blocks: string[] = [];
  const stash = (html: string) => {
    const token = `@@BLOCK${blocks.length}@@`;
    blocks.push(html);
    return token;
  };
  const fenced = text.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_all, lang: string, code: string) => {
    const chart = chartFence(lang, code);
    if (chart) return stash(chart);
    const body = escapeHtml(code.replace(/\n$/, ""));
    if (String(lang || "").toLowerCase() === "mermaid") {
      return stash(`<pre><code class="language-mermaid">${body}</code></pre>`);
    }
    return stash(`<pre><code>${body}</code></pre>`);
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
  for (const line of lines) {
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
  return out
    .join("\n")
    .replace(/@@BLOCK(\d+)@@/g, (_all, index: string) => blocks[Number(index)] ?? "");
}
