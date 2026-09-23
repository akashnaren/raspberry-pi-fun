import { join } from "node:path";
import { createKillSwitch } from "./kill-switch.js";

export const MESH_PAUSE_FILENAME = "MESH_PAUSED";

/** File-backed mesh pause. Presence of data/MESH_PAUSED stops mesh jobs only. */
export function createMeshPause({ dataRoot, filePath } = {}) {
  return createKillSwitch({ filePath: filePath || join(dataRoot, MESH_PAUSE_FILENAME) });
}

export function meshView({ settings, paused = false, lastPeer = "" } = {}) {
  const view = {
    enabled: Boolean(settings?.enabled),
    paused: Boolean(paused),
    target: settings?.target || "auto",
  };
  if (settings?.url) view.url = settings.url;
  if (settings?.kind) view.kind = settings.kind;
  if (lastPeer) view.lastPeer = lastPeer;
  return view;
}
