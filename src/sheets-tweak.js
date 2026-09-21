/** Nova's dry-run Sheets ship. Idempotent. Paste fills from the selected cell. */

export const NOVA_SHEETS_MARK = 'id="paste-from"';

export function applyNovaSheetsTweak(html) {
  const src = String(html || "");
  if (!src.includes("<html")) return src;
  if (src.includes(NOVA_SHEETS_MARK) && src.includes("Paste fills from the selected cell")) {
    return src;
  }

  let next = src;
  if (!next.includes(NOVA_SHEETS_MARK) && next.includes("</header>")) {
    next = next.replace(
      "</header>",
      `</header>
    <label class="paste-label" for="paste-from">Paste</label>
    <textarea id="paste-from" rows="3" placeholder="CSV or TSV"></textarea>`,
    );
  }
  if (!next.includes("Paste fills from the selected cell")) {
    if (next.includes('id="kept"')) {
      next = next.replace(
        /(<span id="kept">)([^<]*)(<\/span>)/,
        '$1Paste fills from the selected cell. $2$3',
      );
    } else if (next.includes("</footer>")) {
      next = next.replace(
        "</footer>",
        '<span>Paste fills from the selected cell.</span></footer>',
      );
    }
  }
  return next;
}
