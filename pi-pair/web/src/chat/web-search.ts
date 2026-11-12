/** Session-only Search control. Unset is on. "0" is an explicit off for this session. */

export const WEB_SEARCH_KEY = "openpi.webSearch";

export function readWebSearch(storage: Storage | null): boolean {
  if (!storage) return true;
  try {
    return storage.getItem(WEB_SEARCH_KEY) !== "0";
  } catch {
    return true;
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
