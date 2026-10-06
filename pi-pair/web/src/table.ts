/** A markdown table. Light HTML, no extra package. */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function cell(value: unknown): string {
  return escapeHtml(String(value ?? "").replace(/\s+/g, " ").trim()).slice(0, 80);
}

export function tableHtml(title: string, columns: string[], rows: string[][]): string | null {
  const heads = columns.map(cell).filter(Boolean).slice(0, 8);
  if (heads.length < 2) return null;
  const body = rows
    .slice(0, 24)
    .map((row) => {
      const cells = heads.map((_head, index) => `<td>${cell(row[index] ?? "")}</td>`).join("");
      return `<tr>${cells}</tr>`;
    })
    .filter((row) => !/^<tr>(?:<td><\/td>)+<\/tr>$/.test(row));
  if (!body.length) return null;
  const caption = title.trim() ? `<caption>${cell(title)}</caption>` : "";
  const head = `<thead><tr>${heads.map((item) => `<th>${item}</th>`).join("")}</tr></thead>`;
  return `<div class="pi-table"><table>${caption}${head}<tbody>${body.join("")}</tbody></table></div>`;
}

export function markdownTable(header: string, rows: string[]): string | null {
  const split = (line: string) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((part) => part.trim());
  const columns = split(header);
  if (columns.length < 2 || columns.some((item) => !item)) return null;
  const body = rows.map(split);
  return tableHtml("", columns, body);
}

export function isTableRule(line: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}
