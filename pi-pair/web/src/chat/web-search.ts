/** Session-only Search control. Off leaves the model check in place. */

export const WEB_SEARCH_KEY = "openpi.webSearch";

export function readWebSearch(storage: Storage | null): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(WEB_SEARCH_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeWebSearch(storage: Storage | null, on: boolean): void {
  if (!storage) return;
  try {
    storage.setItem(WEB_SEARCH_KEY, on ? "1" : "0");
  } catch {
    /* private mode can refuse the write */
  }
}
