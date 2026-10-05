/** Image cards for a visual reply. See docs/IMAGES.md. Empty input renders nothing. */

export interface ImageCard {
  url: string;
  alt: string;
  title: string;
  caption: string;
  source: string;
  width: number;
  height: number;
}

const IMAGE_HOSTS = new Set(["upload.wikimedia.org", "thumb.wikimedia.org"]);

function clip(value: unknown, limit: number): string {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);
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
  if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local")) return null;
  if (blockedIp(host)) return null;
  return url;
}

function blockedIp(host: string): boolean {
  if (host.includes(":")) {
    const bare = host.replace(/^\[|\]$/g, "");
    return bare === "::1" || bare === "::" || bare.startsWith("fc") || bare.startsWith("fd") || bare.startsWith("fe80");
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
  if (!/\.(?:jpg|jpeg|png|webp|gif)$/i.test(url.pathname)) return "";
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

export function cardsFrom(raw: unknown): ImageCard[] {
  if (!Array.isArray(raw)) return [];
  const cards: ImageCard[] = [];
  for (const item of raw) {
    const card = cardFrom(item);
    if (!card || cards.some((have) => have.url === card.url)) continue;
    cards.push(card);
    if (cards.length >= 8) break;
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
