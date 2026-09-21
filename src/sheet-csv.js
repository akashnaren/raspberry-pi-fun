/** Tiny CSV/TSV helpers. The Sheets page inlines the same rules so it stays self-contained. */

export function parseDelimited(text) {
  const src = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!src) return [];
  const delim = src.includes("\t") ? "\t" : ",";
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === '"') {
      quoted = true;
      continue;
    }
    if (ch === delim) {
      row.push(cell);
      cell = "";
      continue;
    }
    if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
      continue;
    }
    cell += ch;
  }
  row.push(cell);
  if (row.some((value) => value !== "") || rows.length === 0) rows.push(row);
  while (rows.length && rows[rows.length - 1].every((value) => value === "")) rows.pop();
  return rows;
}

export function applyPaste(cells, text, startRow = 0, startCol = 0) {
  const block = parseDelimited(text);
  const next = cells.map((row) => row.slice());
  const rows = next.length;
  const cols = rows ? next[0].length : 0;
  for (let r = 0; r < block.length; r += 1) {
    const rr = startRow + r;
    if (rr < 0 || rr >= rows) continue;
    for (let c = 0; c < block[r].length; c += 1) {
      const cc = startCol + c;
      if (cc < 0 || cc >= cols) continue;
      next[rr][cc] = String(block[r][c] ?? "");
    }
  }
  return next;
}

export function toCsv(cells) {
  return cells
    .map((row) =>
      row
        .map((value) => {
          const text = String(value ?? "");
          return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
        })
        .join(","),
    )
    .join("\n");
}

export function emptyGrid(rows, cols) {
  return Array.from({ length: rows }, () => Array.from({ length: cols }, () => ""));
}

export function colLabel(index) {
  return String.fromCharCode(65 + index);
}
