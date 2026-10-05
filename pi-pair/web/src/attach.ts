/** A document stays a card. The model sees the text behind the card. */

export interface DocCard {
  name: string;
  route: string;
  bytes: number;
  excerpt: string;
}

export const DOC_PREVIEW_CHARS = 140;

export function docExcerpt(text: string, limit = DOC_PREVIEW_CHARS): string {
  const line = String(text || "").replace(/\s+/g, " ").trim();
  if (line.length <= limit) return line;
  return line.slice(0, limit).trimEnd() + "…";
}

export function modelUserContent(visible: string, hidden: string): string {
  const show = (visible || "").trim();
  const extra = (hidden || "").trim();
  if (!extra) return show;
  if (!show) return "\n---\n" + extra;
  return show + "\n\n---\n" + extra;
}

export function docKind(route: string): string {
  return route === "ocr" ? "PDF" : "DOC";
}
