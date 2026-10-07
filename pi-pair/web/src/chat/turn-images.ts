import { sessionHeaders } from "../core/session";
import { imageJob, imageJobs, imagePending, ui } from "../core/state";
import type { Turn } from "../core/types";
import { turns } from "../main";
import { fetchImages, lowSubstance, revealImageStrip } from "../render/images";
import { imageWordCount, visibleReply } from "./text";
import { persistTurns, promptBefore } from "./transcript";

export function abortImageJobs(): void {
  for (const job of imageJobs) job.abort();
  imageJobs.clear();
}

export function abortTurnImages(item: Turn): void {
  const job = imageJob.get(item);
  if (!job) return;
  job.abort();
  imageJobs.delete(job);
  imageJob.delete(item);
}

export function wantsImages(item: Turn): boolean {
  if (!ui.picturesOn || item.stopped || item.role !== "assistant") return false;
  if (!visibleReply(item.content) || imageWordCount(item.content) < 2) return false;
  if (item.content.trim().startsWith("I can't help with that.")) return false;
  const sources = item.search?.sources || [];
  if (sources.length) return true;
  const index = turns.indexOf(item);
  if (lowSubstance(promptBefore(index), item.content)) return false;
  return true;
}

export function scheduleImages(row: HTMLElement, item: Turn): void {
  if (item.images && item.images.length) {
    revealImageStrip(row, item.images);
    return;
  }
  if (item.images || !wantsImages(item)) return;
  void loadImages(item);
}

export async function loadImages(item: Turn): Promise<void> {
  if (imagePending.has(item) || item.images || !wantsImages(item)) return;
  imagePending.add(item);
  const ctrl = new AbortController();
  imageJobs.add(ctrl);
  imageJob.set(item, ctrl);
  const question = promptBefore(turns.indexOf(item));
  try {
    const found = await fetchImages({
      question,
      answer: item.content,
      sources: item.search?.sources || [],
      signal: ctrl.signal,
      headers: sessionHeaders(),
    });
    if (ctrl.signal.aborted || !turns.includes(item)) return;
    item.images = found;
    persistTurns();
    const index = turns.indexOf(item);
    const row = document.querySelector('.msg.bot[data-index="' + index + '"]');
    if (row && row.isConnected && found.length) revealImageStrip(row as HTMLElement, found);
  } finally {
    imagePending.delete(item);
    imageJobs.delete(ctrl);
    imageJob.delete(item);
  }
}
