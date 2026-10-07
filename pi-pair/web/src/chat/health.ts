import { byId, el } from "../core/dom";
import { VISIBILITY_SETTLE_MS, serviceView, shouldPollHealth, shouldSoftRetry, softRetryDelay, suppressOfflineBanner } from "../core/presence";
import { ui } from "../core/state";
import type { HealthBody } from "../core/types";
import { FLASH_TIP, PRO_TIP, serviceLines } from "../main";
import { sendText } from "./send";

export function showOffline(): void {
  if (suppressOfflineBanner(document.hidden, ui.resumedAt, Date.now(), undefined, ui.sending)) return;
  const banner = byId("banner");
  if (banner.classList.contains("on")) return;
  banner.className = "on";
  banner.replaceChildren(document.createTextNode("Can't reach the server."));
  const retry = el("button", "banner-retry", "Retry");
  retry.onclick = () => {
    banner.className = "";
    banner.replaceChildren();
    void refresh();
  };
  banner.appendChild(document.createTextNode(" "));
  banner.appendChild(retry);
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function refreshAfterGrace(): void {
  if (ui.graceTimer) return;
  const elapsed = ui.resumedAt > 0 ? Date.now() - ui.resumedAt : 0;
  const wait = Math.max(0, 2500 - elapsed) + 40;
  ui.graceTimer = window.setTimeout(() => {
    ui.graceTimer = 0;
    if (shouldPollHealth(document.hidden)) void refresh();
  }, wait);
}

export async function refresh(): Promise<void> {
  if (!shouldPollHealth(document.hidden)) return;
  for (let attempt = 0; ; attempt += 1) {
    if (!shouldPollHealth(document.hidden)) return;
    try {
      const response = await fetch("/health", { method: "GET", cache: "no-store" });
      if (!response.ok) throw new Error("offline");
      const body = await response.json() as HealthBody;
      const banner = byId("banner");
      banner.className = "";
      banner.replaceChildren();
      paintModelTips();
      paintServices(body);
      return;
    } catch {
      if (suppressOfflineBanner(document.hidden, ui.resumedAt, Date.now(), undefined, ui.sending)) {
        if (ui.sending) ui.healthAfterSend = true;
        else refreshAfterGrace();
        return;
      }
      if (shouldSoftRetry(attempt)) {
        await delay(softRetryDelay(attempt));
        continue;
      }
      showOffline();
      return;
    }
  }
}

export function paintModelTips(): void {
  const write = (id: string, text: string) => {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  };
  write("tip-menu-flash", FLASH_TIP);
  write("tip-set-flash", FLASH_TIP);
  write("tip-menu-pro", PRO_TIP);
  write("tip-set-pro", PRO_TIP);
}

export function paintServices(body: HealthBody): void {
  const now = document.getElementById("serviceNow");
  const log = document.getElementById("serviceLog");
  if (!now || !log) return;
  const view = serviceView(body);
  now.textContent = view.now;
  if (view.signature !== ui.serviceSig) {
    ui.serviceSig = view.signature;
    const stamp = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    serviceLines.unshift(stamp + " · " + view.event);
    if (serviceLines.length > 8) serviceLines.length = 8;
  }
  log.replaceChildren();
  serviceLines.forEach((entry) => {
    log.appendChild(el("p", "service-line", entry));
  });
}

export async function quietNetwork(): Promise<boolean> {
  if (suppressOfflineBanner(document.hidden, ui.resumedAt, Date.now())) return true;
  await delay(VISIBILITY_SETTLE_MS);
  return suppressOfflineBanner(document.hidden, ui.resumedAt, Date.now());
}

export function armResumeSend(text: string, spoken: boolean): void {
  ui.resumeSend = () => {
    void sendText(text, true, spoken);
  };
}

export function flushDeferredHealth(): void {
  if (!ui.healthAfterSend) return;
  ui.healthAfterSend = false;
  if (shouldPollHealth(document.hidden)) void refresh();
}
