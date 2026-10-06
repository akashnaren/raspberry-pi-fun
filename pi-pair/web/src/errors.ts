/** Plain sentences for the page. Raw causes stay on the server. */

export const BUSY_LINE = "Too many chats are going at once. Try again in a moment.";
export const WAITING_LINE = "Waiting for a free slot…";
export const DROP_LINE = "Connection dropped. Try again.";
export const GENERIC_LINE = "The reply did not come back. Try again.";
export const SLOW_LINE = "That took too long. Try again.";
export const BIG_LINE = "That is too big to send. Try a shorter message.";
export const UNREACHABLE_LINE = "The chat service is not reachable. Try again.";
export const NO_CHAT_LINE = "That machine cannot answer chats.";
export const MODEL_LINE = "The larger model is not ready yet.";
export const OCR_LINE = "This device cannot read pictures or scanned pages.";
export const OCR_BUSY_LINE = "Reading a file is busy. Try again in a moment.";
export const FILE_LINE = "That file type is not supported.";
export const FILE_SEND_LINE = "That file could not be sent. Try again.";
export const EMPTY_LINE = "That file is empty.";
export const PDF_LINE = "That PDF could not be read. Try a text file or a photo.";
export const PICTURE_LINE = "Pictures could not be loaded.";
export const SEARCH_LINE = "Search is not available from here.";

const BANNED = /\b(pi[234]|ollama|traceback|generations in flight)\b|\bHTTP\b|\{|\[/i;

export function friendlyError(raw: unknown): string {
  const text = String(raw ?? "").replace(/\s+/g, " ").trim();
  if (!text) return GENERIC_LINE;
  if (/Load failed|Failed to fetch|NetworkError|\bnetwork\b|AbortError|\babort\b/i.test(text)) {
    return DROP_LINE;
  }
  if (/at capacity|generations in flight/i.test(text)) return BUSY_LINE;
  if (/took too long|timed out|timeout/i.test(text)) return SLOW_LINE;
  if (/too long|too large|over 4 mb|\b413\b/i.test(text)) return BIG_LINE;
  if (/jpeg-scanned|no readable text/i.test(text)) return PDF_LINE;
  if (/not installed|this pi/i.test(text)) return OCR_LINE;
  if (/ocr is busy/i.test(text)) return OCR_BUSY_LINE;
  if (/unsupported file/i.test(text)) return FILE_LINE;
  if (/attachment is empty|file is empty/i.test(text)) return EMPTY_LINE;
  if (/multipart|content-length|must be a file/i.test(text)) return FILE_SEND_LINE;
  if (/cannot be the brain|does not run a chat model/i.test(text)) return NO_CHAT_LINE;
  if (/unreachable|offline|no usable model|unknown peer/i.test(text)) return UNREACHABLE_LINE;
  if (/does not pull|ollama pull|not on pi/i.test(text)) return MODEL_LINE;
  if (/health host/i.test(text)) return SEARCH_LINE;
  if (BANNED.test(text) || text.length > 180 || /\b[45]\d\d\b/.test(text)) return GENERIC_LINE;
  return text;
}
