import type { DocCard } from "../attach/message";
import { cancelAttach } from "../attach/upload";
import { byId } from "../core/dom";
import { ui } from "../core/state";
import { followExtra } from "../main";
import { paintBrand } from "../ui/brand";
import { voiceNote } from "../voice/mode";
import { dropFollow, enqueueFollow, renderFollowQueue, takeFollow } from "./follow-queue";
import { autoGrow, sendText, syncSend } from "./send";

export function paintFollowQueue(): void {
  renderFollowQueue(byId("followQueue"), ui.followQueue, (id) => {
    ui.followQueue = dropFollow(ui.followQueue, id);
    followExtra.delete(id);
    paintFollowQueue();
    syncSend();
  });
}

export function clearFollowQueue(): void {
  ui.followQueue = [];
  followExtra.clear();
  paintFollowQueue();
}

export function queueDraft(text: string, hidden: string, attachment: DocCard | null): void {
  const label = text || (attachment && attachment.name) || "Attachment";
  const result = enqueueFollow(ui.followQueue, label, ui.followNextId);
  if (!result.added) {
    voiceNote("Three messages are already waiting.");
    return;
  }
  ui.followQueue = result.items;
  ui.followNextId = result.nextId;
  const item = result.items[result.items.length - 1];
  followExtra.set(item.id, { text, hidden: hidden.trim(), attachment });
  const box = byId<HTMLTextAreaElement>("q");
  if (hidden.trim() || attachment) cancelAttach();
  box.value = "";
  autoGrow(box);
  paintBrand();
  paintFollowQueue();
  syncSend();
}

export function flushFollowQueue(): void {
  if (ui.sending) return;
  const taken = takeFollow(ui.followQueue);
  if (!taken.next) return;
  ui.followQueue = taken.rest;
  const extra = followExtra.get(taken.next.id);
  followExtra.delete(taken.next.id);
  paintFollowQueue();
  void sendText(extra ? extra.text : taken.next.text, false, false, {
    hidden: extra?.hidden || "",
    attachment: extra?.attachment || null,
  });
}
