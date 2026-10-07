import { browserStorage } from "../core/settings";
import { SPLASH_HOLD_MS, SPLASH_KEY, appendBrandMark, navigationType, shouldPlaySplash } from "./splash";

export function paintBrand(typing?: boolean): void {
  const brand = document.getElementById("brand");
  const box = document.getElementById("q") as HTMLTextAreaElement | null;
  if (!brand || !box) return;
  const on = typeof typing === "boolean" ? typing : Boolean(box.value);
  brand.classList.toggle("brand-title", on);
  brand.classList.toggle("brand-logo", !on);
}

export function bootSplash(): void {
  const mark = document.getElementById("brandMark");
  if (mark) appendBrandMark(document, mark);
  paintBrand();
  const store = browserStorage("session");
  const seen = store ? store.getItem(SPLASH_KEY) : null;
  if (!shouldPlaySplash(seen, navigationType())) return;
  if (store) store.setItem(SPLASH_KEY, "1");
  const brand = document.getElementById("brand");
  if (!brand) return;
  brand.classList.add("brand-enter");
  window.setTimeout(() => brand.classList.remove("brand-enter"), SPLASH_HOLD_MS);
}
