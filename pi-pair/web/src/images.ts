/** Image cards for a finished reply. See docs/IMAGES.md. Empty input renders nothing. */

export interface ImageCard {
  url: string;
  alt: string;
  title: string;
  caption: string;
  source: string;
  width: number;
  height: number;
}

export interface ImageRequest {
  question: string;
  answer: string;
  sources?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

const IMAGE_HOSTS = new Set(["upload.wikimedia.org", "thumb.wikimedia.org"]);
const MAX_CARDS = 4;

function clip(value: unknown, limit: number): string {
  return String(value ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, limit);
}

function parseHttp(value: unknown): URL | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.startsWith("//")) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.username || url.password) return null;
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.port && url.port !== "80" && url.port !== "443") return null;
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) {
    return null;
  }
  if (blockedIp(host)) return null;
  return url;
}

function blockedIp(host: string): boolean {
  if (host.includes(":")) {
    const bare = host.replace(/^\[|\]$/g, "");
    return (
      bare === "::1" ||
      bare === "::" ||
      bare.startsWith("fc") ||
      bare.startsWith("fd") ||
      bare.startsWith("fe80")
    );
  }
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!match) return false;
  const parts = match.slice(1).map(Number);
  if (parts.some((part) => part > 255)) return true;
  const a = parts[0];
  const b = parts[1];
  if (a === 0 || a === 10 || a === 127 || a >= 224) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 100 && b >= 64 && b <= 127) return true;
  return false;
}

function imageUrl(value: unknown): string {
  const url = parseHttp(value);
  if (!url || !IMAGE_HOSTS.has(url.hostname.toLowerCase())) return "";
  const path = url.pathname.toLowerCase();
  if (path.includes(".svg")) return "";
  if (!/\.(?:jpg|jpeg|webp|png)$/i.test(path)) return "";
  return url.toString();
}

function pageUrl(value: unknown): string {
  const url = parseHttp(value);
  if (!url || url.protocol !== "https:") return "";
  const host = url.hostname.toLowerCase();
  if (host !== "wikipedia.org" && !host.endsWith(".wikipedia.org")) return "";
  return url.toString();
}

function pixels(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0 || value > 8000) return 0;
  return value;
}

export function cardFrom(raw: unknown): ImageCard | null {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const url = imageUrl(item.url);
  const title = clip(item.title || item.alt, 120);
  const alt = clip(item.alt || title, 180);
  if (!url || !title || !alt) return null;
  return {
    url,
    alt,
    title,
    caption: clip(item.caption, 140),
    source: pageUrl(item.source),
    width: pixels(item.width),
    height: pixels(item.height),
  };
}

const IMAGE_MIN_ANSWER_WORDS = 12;
const LIST_LINE = /^\s*(?:\d{1,2}[.)]\s+|[-*]\s+)/m;

function outsideFences(text: string): string {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, "\n")
    .replace(/```[\s\S]*$/g, "\n");
}

function namedEntityCue(question: string): boolean {
  const text = String(question || "");
  const tokens = text.matchAll(/[^\W\d_]+/gu);
  for (const token of tokens) {
    const word = token[0];
    if (word.length < 2) continue;
    const first = word[0];
    if (first.toUpperCase() === first.toLowerCase() || first !== first.toUpperCase()) continue;
    const before = text.slice(0, token.index ?? 0).trimEnd();
    if (!before) continue;
    const mark = before[before.length - 1];
    if (mark === "." || mark === "!" || mark === "?") continue;
    return true;
  }
  return false;
}

/** True when a turn has no list, bold, or topical cue, so image search can wait. */
export function lowSubstance(question: string, answer: string): boolean {
  const plain = outsideFences(answer);
  if (plain.includes("**") || LIST_LINE.test(plain)) return false;
  const words = plain.trim().split(/\s+/).filter(Boolean);
  const informative = words.length >= IMAGE_MIN_ANSWER_WORDS && !plain.trim().endsWith("?");
  if (informative || namedEntityCue(question)) return false;
  return true;
}

export function cardsFrom(raw: unknown): ImageCard[] {
  if (!Array.isArray(raw)) return [];
  const cards: ImageCard[] = [];
  for (const item of raw) {
    const card = cardFrom(item);
    if (!card || cards.some((have) => have.url === card.url)) continue;
    cards.push(card);
    if (cards.length >= MAX_CARDS) break;
  }
  return cards;
}

function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderImageCardsHtml(raw: unknown): string {
  const cards = cardsFrom(raw);
  if (!cards.length) return "";
  const figures = cards.map((card) => {
    const size = card.width && card.height ? ` width="${card.width}" height="${card.height}"` : "";
    const img = `<img src="${esc(card.url)}" alt="${esc(card.alt)}"${size} loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
    const caption = card.caption ? `<span class="image-card-caption">${esc(card.caption)}</span>` : "";
    const copy = `<span class="image-card-copy"><span class="image-card-title">${esc(card.title)}</span>${caption}</span>`;
    if (card.source) {
      return `<a class="image-card" href="${esc(card.source)}" target="_blank" rel="noopener noreferrer">${img}${copy}</a>`;
    }
    return `<figure class="image-card">${img}${copy}</figure>`;
  });
  return `<div class="image-cards" aria-label="Images">${figures.join("")}</div>`;
}

function sourceRows(raw: unknown): { title: string; url: string }[] {
  if (!Array.isArray(raw)) return [];
  const rows: { title: string; url: string }[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      rows.push({ title: "", url: item });
    } else if (item && typeof item === "object") {
      const row = item as { title?: unknown; url?: unknown };
      rows.push({
        title: typeof row.title === "string" ? row.title : "",
        url: typeof row.url === "string" ? row.url : "",
      });
    }
    if (rows.length >= 8) break;
  }
  return rows;
}

export async function fetchImages(input: ImageRequest): Promise<ImageCard[]> {
  try {
    const response = await fetch("/v1/images", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(input.headers || {}),
      },
      body: JSON.stringify({
        question: String(input.question || "").slice(0, 500),
        answer: String(input.answer || "").slice(0, 4000),
        sources: sourceRows(input.sources),
      }),
      signal: input.signal,
      cache: "no-store",
    });
    if (!response.ok) return [];
    const payload = (await response.json()) as { pi_images?: unknown };
    return cardsFrom(payload?.pi_images);
  } catch {
    return [];
  }
}

function childByClass(parent: HTMLElement, name: string): HTMLElement | null {
  for (const child of parent.children) {
    if (child.classList.contains(name)) return child as HTMLElement;
  }
  return null;
}

function insertAfterBody(parent: HTMLElement, strip: HTMLElement): void {
  const body = childByClass(parent, "body");
  if (body) {
    body.insertAdjacentElement("afterend", strip);
    return;
  }
  parent.appendChild(strip);
}

export function revealImageStrip(parent: HTMLElement, raw: unknown): void {
  const cards = cardsFrom(raw);
  if (!cards.length || !parent.isConnected) return;
  const ready: Array<ImageCard | null> = cards.map(() => null);

  const paint = () => {
    if (!parent.isConnected) return;
    const shown = ready.filter((card): card is ImageCard => card !== null);
    const existing = childByClass(parent, "image-cards");
    if (!shown.length) {
      existing?.remove();
      return;
    }
    const holder = document.createElement("div");
    holder.innerHTML = renderImageCardsHtml(shown);
    const strip = holder.firstElementChild as HTMLElement | null;
    if (!strip) return;
    strip.querySelectorAll("img").forEach((img) => {
      img.addEventListener("error", () => {
        const card = img.closest(".image-card");
        card?.remove();
        if (!strip.querySelector(".image-card")) strip.remove();
      });
    });
    if (existing) existing.replaceWith(strip);
    else insertAfterBody(parent, strip);
  };

  cards.forEach((card, index) => {
    const preload = new Image();
    preload.referrerPolicy = "no-referrer";
    preload.onload = () => {
      ready[index] = card;
      paint();
    };
    preload.onerror = () => {
      paint();
    };
    preload.src = card.url;
  });
}
