/** Nova's dry-run Docs ship. Headings, lists, find, print. Idempotent. */

export const NOVA_DOCS_MARK = 'id="save-md"';
export const NOVA_DOCS_LEAP = 'id="find-box"';

export function applyNovaDocsTweak(html) {
  const src = String(html || "");
  if (!src.includes("<html")) return src;
  if (
    src.includes(NOVA_DOCS_MARK) &&
    src.includes("function downloadMarkdown") &&
    src.includes(NOVA_DOCS_LEAP) &&
    src.includes("function printReadyHtml") &&
    src.includes('data-heading="1"') &&
    src.includes("insertOrderedList") &&
    src.includes('id="insert-table"') &&
    src.includes('id="preview-toggle"')
  ) {
    return src;
  }

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

  if (!next.includes('data-heading="1"') && next.includes('data-heading="2"')) {
    next = next.replace(
      '<button type="button" data-heading="2"',
      '<button type="button" data-heading="1" aria-pressed="false">H1</button>\n          <button type="button" data-heading="2"',
    );
  }

  if (!next.includes("insertOrderedList") && next.includes('data-cmd="insertUnorderedList"')) {
    next = next.replace(
      '<button type="button" data-cmd="insertUnorderedList" aria-pressed="false">•</button>',
      '<button type="button" data-cmd="insertUnorderedList" aria-pressed="false">•</button>\n          <button type="button" data-cmd="insertOrderedList" aria-pressed="false">1.</button>',
    );
  }

  if (!next.includes(NOVA_DOCS_LEAP)) {
    if (next.includes("</header>")) {
      next = next.replace(
        "</header>",
        `</header>
    <div id="find-box" class="find-box" hidden>
      <label for="find-q">Find</label>
      <input id="find-q" type="search" />
      <span id="find-count">0</span>
    </div>`,
      );
    } else if (next.includes("</footer>")) {
      next = next.replace(
        "</footer>",
        '<div id="find-box" hidden><input id="find-q" type="search" /></div></footer>',
      );
    }
  }

  if (!next.includes('id="insert-table"') && next.includes('id="find-open"')) {
    next = next.replace(
      '<button type="button" id="find-open">Find</button>',
      '<button type="button" id="insert-table">Table</button>\n          <button type="button" id="preview-toggle" aria-pressed="false">Preview</button>\n          <button type="button" id="find-open">Find</button>',
    );
  }

  if (!next.includes("function printReadyHtml")) {
    const helper = `function printReadyHtml(docTitle, docHtml) {
        const heading = String(docTitle || "Untitled").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return "<!doctype html><html><head><meta charset=\\"utf-8\\"><title>" + heading +
          "</title><style>body{margin:2rem auto;max-width:40rem;color:#2a2118;font:1.05rem/1.55 Georgia,serif}h1{font-size:1.8rem}h2{font-size:1.15rem}</style></head><body><h1>" +
          heading + "</h1>" + String(docHtml || "") + "</body></html>";
      }
      `;
    if (next.includes("function download(")) {
      next = next.replace("function download(", `${helper}function download(`);
    } else if (next.includes("</script>")) {
      next = next.replace("</script>", `${helper}</script>`);
    }
  }

  return next;
}
