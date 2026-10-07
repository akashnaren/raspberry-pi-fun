export const els = {} as {
  composer: HTMLTextAreaElement;
  enterBox: HTMLInputElement;
  picturesBox: HTMLInputElement;
  voiceSend: HTMLElement | null;
  voiceEnd: HTMLElement | null;
  memoryButton: HTMLElement | null;
  memoryAnchor: Element | null | undefined;
};

export function el(tag: string, cls?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function byId<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error("missing " + id);
  return node as T;
}
