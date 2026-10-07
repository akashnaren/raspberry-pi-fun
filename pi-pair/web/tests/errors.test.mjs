import { friendlyError, WAITING_LINE } from "../src/core/errors.ts";

const cases = [
  ["", "The reply did not come back. Try again."],
  ["Failed to fetch", "Connection dropped. Try again."],
  ["pi4 is at capacity (2 generations in flight). Try again in a moment.", "Too many chats are going at once. Try again in a moment."],
  ["that took too long", "That took too long. Try again."],
  ["reading that file took too long", "That took too long. Try again."],
  ["attachment is over 4 MB", "That is too big to send. Try a shorter message."],
  ["pi4 offline", "The chat service is not reachable. Try again."],
  ["pi2 cannot be the brain", "That machine cannot answer chats."],
  ["ollama pull qwen2.5:1.5b", "The larger model is not ready yet."],
  ["OCR is not installed on this Pi", "This device cannot read pictures or scanned pages."],
  ["OCR is busy", "Reading a file is busy. Try again in a moment."],
  ["unsupported file type", "That file type is not supported."],
  ["only a JPEG-scanned PDF can be read", "That PDF could not be read. Try a text file or a photo."],
  ["that PDF has no readable text", "That PDF could not be read. Try a text file or a photo."],
  ["search is served on the health host", "Search is not available from here."],
  ["could not read that file", "could not read that file"],
  ["HTTP 500", "The reply did not come back. Try again."],
];
for (const [raw, want] of cases) {
  const shown = friendlyError(raw);
  if (shown !== want) throw new Error(JSON.stringify(raw) + " -> " + shown);
  if (!shown.trim()) throw new Error("empty failure");
}
if (!WAITING_LINE.includes("Waiting for a free slot")) {
  throw new Error("status lines drifted");
}
console.log("ok");
