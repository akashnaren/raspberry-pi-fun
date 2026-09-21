/** Messy paste → tidy rows. Catalogue utility; Sheets keeps its own grid parser. */

export function detectDelimiter(text) {
  const src = String(text ?? "");
  if (src.includes("\t")) return "\t";
  const lines = src.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n").filter((line) => line.trim());
  if (!lines.length) return ",";
  const commas = lines.map((line) => (line.match(/,/g) || []).length);
  const spaces = lines.filter((line) => /\s\s+/.test(line)).length;
  if (Math.max(...commas) > 0 && spaces < lines.length / 2) return ",";
  if (spaces > 0) return "spaces";
  const singles = lines.map((line) => line.trim().split(/\s+/).length);
  if (singles.every((n) => n > 1) && new Set(singles).size === 1) return "space";
  return ",";
}

export function tidyRows(text) {
  const src = String(text ?? "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!src.trim()) return [];
  const delim = detectDelimiter(src);
  const rows = delim === "\t" || delim === "," ? parseQuoted(src, delim) : parseSpaces(src, delim === "spaces");
  const width = rows.reduce((max, row) => Math.max(max, row.length), 0);
  return rows
    .map((row) => {
      const next = row.map((cell) => String(cell ?? "").trim());
      while (next.length < width) next.push("");
      return next;
    })
    .filter((row) => row.some((cell) => cell !== ""));
}

function parseQuoted(src, delim) {
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
  return rows;
}

function parseSpaces(src, multi) {
  return src.split("\n").map((line) => {
    const trimmed = line.trim();
    if (!trimmed) return [];
    return trimmed.split(multi ? /\s\s+/ : /\s+/);
  });
}

export function toCsv(rows) {
  return (rows || [])
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
