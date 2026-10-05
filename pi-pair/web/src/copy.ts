/** User-facing labels. Routing internals stay off the page and out of replies. */

const META =
  /\b(?:thinking\s+(?:level|control|effort)|(?:low|medium|high)\s+(?:thinking|effort)|effort\s+(?:level|setting)|flash\s+map|(?:flash|pro)\s+(?:mode|model|route)|auto\s+mode|model\s+routing|routing\s+label|private\s+(?:pi\s+)?mesh|pi\s+private\s+mesh|mesh\s+assistant|canned\s+map|generative\s+brain)\b/i;

export function modeChipText(mode: string, route: string): string {
  const tier = route === "pro" ? "Pro" : route === "flash" ? "Flash" : "";
  if (mode === "auto") return tier ? "Auto · " + tier : "Auto";
  if (mode === "flash") return "Flash";
  if (mode === "pro") return "Pro";
  return "";
}

function dropMeta(prose: string): string {
  if (!META.test(prose)) return prose;
  const kept: string[] = [];
  prose.split("\n").forEach((line) => {
    if (/^\s*\d+\.\s+\S/.test(line) && !META.test(line)) {
      kept.push(line);
      return;
    }
    const good = line
      .split(/(?<=[.!?])\s+/)
      .filter((piece) => piece.trim() && !META.test(piece));
    if (good.length) kept.push(good.join(" "));
  });
  return kept.join("\n");
}

/** Remove effort, routing, and mesh sentences. Fenced blocks stay. */
export function scrubAssistant(text: string): string {
  const raw = String(text || "");
  if (!META.test(raw)) return raw;
  const parts = raw.split(/(```[\s\S]*?```)/);
  const cleaned = parts.map((part, index) => (index % 2 === 1 ? part : dropMeta(part)));
  return cleaned
    .join("")
    .split("\n")
    .filter((line) => line.trim())
    .join("\n")
    .trim();
}
