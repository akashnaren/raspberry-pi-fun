/** Follow-ups typed while a reply is still streaming. FIFO, capped. */

export const FOLLOW_LIMIT = 3;

export interface FollowItem {
  id: number;
  text: string;
}

export function enqueueFollow(
  queue: FollowItem[],
  text: string,
  nextId: number,
): { items: FollowItem[]; nextId: number; added: boolean } {
  const trimmed = text.trim();
  if (!trimmed || queue.length >= FOLLOW_LIMIT) {
    return { items: queue, nextId, added: false };
  }
  return {
    items: [...queue, { id: nextId, text: trimmed }],
    nextId: nextId + 1,
    added: true,
  };
}

export function dropFollow(queue: FollowItem[], id: number): FollowItem[] {
  return queue.filter((item) => item.id !== id);
}

export function takeFollow(queue: FollowItem[]): { next: FollowItem | null; rest: FollowItem[] } {
  if (!queue.length) return { next: null, rest: queue };
  return { next: queue[0], rest: queue.slice(1) };
}

export function renderFollowQueue(
  host: HTMLElement,
  queue: FollowItem[],
  onRemove: (id: number) => void,
): void {
  host.replaceChildren();
  host.hidden = queue.length === 0;
  queue.forEach((item) => {
    const chip = document.createElement("div");
    chip.className = "follow-chip";
    const label = document.createElement("span");
    label.className = "follow-tag";
    label.textContent = "Queued";
    const body = document.createElement("span");
    body.className = "follow-text";
    body.textContent = item.text;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "follow-drop";
    remove.setAttribute("aria-label", "Remove queued message");
    remove.textContent = "×";
    remove.addEventListener("click", () => onRemove(item.id));
    chip.append(label, body, remove);
    host.appendChild(chip);
  });
}
