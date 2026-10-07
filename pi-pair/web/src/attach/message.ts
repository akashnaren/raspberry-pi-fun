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

export interface UserPiece {
  kind: "card" | "text";
  card?: DocCard;
  text: string;
}

/** Card first, then the question. The card holds a short preview, never the OCR body. */
export function userMessagePieces(question: string, card: DocCard | null): UserPiece[] {
  const pieces: UserPiece[] = [];
  if (card) {
    const preview = docExcerpt(card.excerpt);
    pieces.push({
      kind: "card",
      card: { ...card, excerpt: preview },
      text: preview,
    });
  }
  const visible = (question || "").trim();
  if (visible) pieces.push({ kind: "text", text: visible });
  return pieces;
}
