/** Print-ready Docs HTML. Chrome stays off the page a stranger prints. */

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function stripFindMarks(html) {
  return String(html ?? "")
    .replace(/<mark class="find-hit">/gi, "")
    .replace(/<\/mark>/gi, "");
}

export function printReadyHtml(title, bodyHtml) {
  const heading = String(title || "Untitled").trim() || "Untitled";
  const body = stripFindMarks(bodyHtml);
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${escapeHtml(heading)}</title>
<style>
  body { margin: 2.2rem auto; max-width: 40rem; padding: 0 1.2rem 3rem; color: #2a2118; background: #fff; font: 1.05rem/1.55 Georgia, "Iowan Old Style", serif; }
  h1 { font-size: 1.8rem; margin: 0 0 0.9rem; font-weight: 600; }
  h2 { font-size: 1.15rem; margin: 1.3rem 0 0.4rem; font-weight: 600; }
  p { margin: 0 0 0.75rem; }
  ul, ol { margin: 0 0 0.75rem; padding-left: 1.3rem; }
  li { margin: 0 0 0.25rem; }
</style>
</head>
<body>
<h1>${escapeHtml(heading)}</h1>
${body}
</body>
</html>
`;
}

export function findHits(text, query) {
  const hay = String(text ?? "");
  const needle = String(query ?? "").trim();
  if (!needle) return [];
  const lower = hay.toLowerCase();
  const q = needle.toLowerCase();
  const hits = [];
  let from = 0;
  while (from < lower.length) {
    const at = lower.indexOf(q, from);
    if (at < 0) break;
    hits.push({ start: at, end: at + needle.length });
    from = at + needle.length;
  }
  return hits;
}
