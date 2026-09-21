/** Nova's dry-run Docs ship. Idempotent. Never invents a second flagship. */

export const NOVA_DOCS_MARK = 'id="save-md"';

export function applyNovaDocsTweak(html) {
  const src = String(html || "");
  if (!src.includes("<html")) return src;
  if (src.includes(NOVA_DOCS_MARK) && src.includes("function downloadMarkdown")) return src;

  let next = src;
  if (!next.includes(NOVA_DOCS_MARK)) {
    if (next.includes('<span id="kept">')) {
      next = next.replace(
        '<span id="kept">',
        '<span id="save-md">Ctrl+S downloads .md</span>\n      <span id="kept">',
      );
    } else if (next.includes("</footer>")) {
      next = next.replace("</footer>", '<span id="save-md">Ctrl+S downloads .md</span></footer>');
    }
  }

  if (!next.includes("function downloadMarkdown")) {
    if (next.includes('document.getElementById("download-md")')) {
      next = next.replace(
        'document.getElementById("download-md").addEventListener("click", function () {',
        `function downloadMarkdown() {
        const md = "# " + title.value.trim() + "\\n\\n" + htmlToMarkdown(body);
        download(fileBase() + ".md", "text/markdown;charset=utf-8", md);
      }
      document.getElementById("download-md").addEventListener("click", function () {`,
      );
      next = next.replace(
        /const md = "# " \+ title\.value\.trim\(\) \+ "\\n\\n" \+ htmlToMarkdown\(body\);\s*download\(fileBase\(\) \+ "\.md", "text\/markdown;charset=utf-8", md\);/,
        "downloadMarkdown();",
      );
    }
  }

  if (!next.includes('key === "s"') && next.includes('key === "b"')) {
    next = next.replace(
      'if (key === "b" || key === "i") {',
      `if (key === "s") {
          event.preventDefault();
          if (typeof downloadMarkdown === "function") downloadMarkdown();
          return;
        }
        if (key === "b" || key === "i") {`,
    );
  }

  if (!next.includes("Ctrl+S writes a .md") && next.includes("Write here. This tab keeps the page")) {
    next = next.replace(
      "Write here. This tab keeps the page on this machine. Download when you want a file.",
      "Write here. Ctrl+S writes a .md. Download is a file, not a tab.",
    );
  }

  return next;
}
