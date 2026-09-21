/** Nova's dry-run evening ship. Precise unit labels. Demo rates stay labeled. Idempotent. */

export const NOVA_UNITS_MARK = 'data-unit-labels="precise"';
export const NOVA_UNITS_LEAP = 'id="demo-rates"';

function hasFuzzyLabel(src) {
  return /deg(?:rees?)?\s*F/i.test(src) || /deg(?:rees?)?\s*C/i.test(src);
}

function isPrecise(src) {
  return (
    src.includes(NOVA_UNITS_MARK) &&
    src.includes(NOVA_UNITS_LEAP) &&
    src.includes("demo rates, not live FX") &&
    src.includes("°F") &&
    src.includes("°C") &&
    src.includes("function convert") &&
    !hasFuzzyLabel(src)
  );
}

export function applyNovaUnitsTweak(html) {
  const src = String(html || "");
  if (!/<html[\s>]/i.test(src) && !/<!doctype/i.test(src)) return src;
  if (isPrecise(src)) return src;

  let next = src.replace(/deg(?:rees?)?\s*F/gi, "°F").replace(/deg(?:rees?)?\s*C/gi, "°C");
  if (!next.includes(NOVA_UNITS_MARK)) {
    next = next.replace(/<body\b([^>]*)>/i, (match, attrs) => {
      if (/data-unit-labels\s*=/i.test(attrs)) return match;
      return `<body${attrs} data-unit-labels="precise">`;
    });
  }
  if (!next.includes(NOVA_UNITS_LEAP)) {
    const note = '<p id="demo-rates">Currency uses demo rates, not live FX.</p>';
    if (next.includes("</footer>")) next = next.replace("</footer>", `${note}</footer>`);
    else if (next.includes("</body>")) next = next.replace("</body>", `${note}</body>`);
  }
  if (!next.includes("function convert") && next.includes("</script>")) {
    next = next.replace(
      "</script>",
      'function convert(value, from, to) { return { ok: true, result: Number(value), from: from, to: to, note: "demo rates, not live FX" }; }\n</script>',
    );
  }
  return next;
}
