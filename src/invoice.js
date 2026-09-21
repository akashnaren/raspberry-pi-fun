/** Paste lines → a cream invoice or estimate. Local only. No payments. */

export function parseMoney(value) {
  const n = Number(String(value ?? "").replace(/[$,]/g, "").trim());
  return Number.isFinite(n) ? n : 0;
}

export function parseItemLine(line) {
  const src = String(line ?? "").trim();
  if (!src) return null;
  const qtyAt = /^(.+?)\s+[x×]\s*(\d+(?:\.\d+)?)\s+@\s*\$?(\d+(?:\.\d+)?)\s*$/i.exec(src);
  if (qtyAt) {
    return { desc: qtyAt[1].trim(), qty: Number(qtyAt[2]), price: Number(qtyAt[3]) };
  }
  if (src.includes(",") || src.includes("\t")) {
    const parts = src.split(/[,\t]/).map((cell) => cell.trim());
    if (parts.length >= 3) {
      const price = parseMoney(parts[parts.length - 1]);
      const qty = parseMoney(parts[parts.length - 2]);
      if (qty > 0) {
        return { desc: parts.slice(0, -2).join(", "), qty, price };
      }
    }
  }
  const spaced = src.split(/\s{2,}|\s+/);
  if (spaced.length >= 3) {
    const price = parseMoney(spaced[spaced.length - 1]);
    const qty = parseMoney(spaced[spaced.length - 2]);
    if (qty > 0) {
      return { desc: spaced.slice(0, -2).join(" "), qty, price };
    }
  }
  return null;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function money(n) {
  return (Math.round(Number(n || 0) * 100) / 100).toFixed(2);
}

export function parseInvoice(text) {
  const lines = String(text ?? "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !/^[-–—_=]{2,}$/.test(line));
  const meta = { kind: "invoice", from: "", to: "", number: "", date: "" };
  const items = [];
  for (const line of lines) {
    const itemEarly = parseItemLine(line);
    if (/^estimate\b/i.test(line) && !itemEarly) {
      meta.kind = "estimate";
      const rest = line.replace(/^estimate\b[:\s]*/i, "").trim();
      if (rest) meta.number = meta.number || rest;
      continue;
    }
    if (/^invoice\b/i.test(line) && !itemEarly) {
      meta.kind = "invoice";
      const rest = line.replace(/^invoice\b[:\s]*/i, "").trim();
      if (rest) meta.number = meta.number || rest;
      continue;
    }
    const kv = /^(from|to|bill(?:\s+to)?|client|number|no\.?|date|kind|type)\s*[:#]\s*(.+)$/i.exec(line);
    if (kv) {
      let key = kv[1].toLowerCase();
      if (key.startsWith("bill") || key === "client") key = "to";
      if (key === "no" || key === "no.") key = "number";
      if (key === "type") key = "kind";
      const val = kv[2].trim();
      if (key === "kind") meta.kind = /est/i.test(val) ? "estimate" : "invoice";
      else meta[key] = val;
      continue;
    }
    if (/^item\b/i.test(line) && /qty|price|amount/i.test(line)) continue;
    const item = itemEarly;
    if (item && item.desc && !/^(item|desc|description)$/i.test(item.desc)) {
      items.push(item);
      continue;
    }
    if (!meta.from) meta.from = line;
    else if (!meta.to) meta.to = line;
    else if (!meta.number) meta.number = line;
  }
  const subtotal = items.reduce((sum, row) => sum + row.qty * row.price, 0);
  const total = Math.round(subtotal * 100) / 100;
  return { ...meta, items, subtotal: total, total };
}

export function invoiceReadyHtml(doc) {
  const kind = doc?.kind === "estimate" ? "Estimate" : "Invoice";
  const heading = escapeHtml(kind);
  const from = escapeHtml(doc?.from || "Meridian Desk");
  const to = escapeHtml(doc?.to || "");
  const number = escapeHtml(doc?.number || "");
  const date = escapeHtml(doc?.date || "");
  const rows = (doc?.items || [])
    .map((row) => {
      const line = money(row.qty * row.price);
      return `<tr><td>${escapeHtml(row.desc)}</td><td>${escapeHtml(String(row.qty))}</td><td>${money(row.price)}</td><td>${line}</td></tr>`;
    })
    .join("");
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${heading}${number ? ` ${number}` : ""}</title>
<style>
  @page { margin: 0.75in; }
  body { margin: 0; padding: 1.4rem 1.6rem 2.4rem; color: #2a2118; background: #faf4e8; font: 1rem/1.45 Georgia, "Iowan Old Style", serif; }
  h1 { font-size: 1.6rem; margin: 0 0 0.35rem; font-weight: 600; }
  .meta { color: #6b5d4d; font-size: 0.92rem; margin: 0 0 1.1rem; }
  table { border-collapse: collapse; width: 100%; margin: 0 0 1rem; }
  th, td { border: 1px solid #d8cbb0; padding: 0.35rem 0.5rem; text-align: left; }
  th { background: #efe6d2; font-weight: 500; }
  td:nth-child(2), td:nth-child(3), td:nth-child(4), th:nth-child(2), th:nth-child(3), th:nth-child(4) { text-align: right; }
  .total { padding-bottom: 1.2rem; font-size: 1.15rem; }
</style>
</head>
<body>
<h1>${heading}</h1>
<p class="meta">${from}${to ? `<br>For ${to}` : ""}${number ? `<br>${number}` : ""}${date ? `<br>${date}` : ""}</p>
<table>
  <thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Amount</th></tr></thead>
  <tbody>${rows || `<tr><td colspan="4">No lines yet.</td></tr>`}</tbody>
</table>
<p class="total">Total $${money(doc?.total || 0)}</p>
</body>
</html>
`;
}
