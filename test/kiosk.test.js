import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { REPO } from "./helpers.js";

test("kiosk unit is Wayland / labwc with ozone, not X11 DISPLAY=:0", async () => {
  const unit = await readFile(join(REPO, "deploy/chromium-kiosk.service"), "utf8");
  assert.match(unit, /WAYLAND_DISPLAY=wayland-0/);
  assert.match(unit, /XDG_RUNTIME_DIR=\/run\/user\/1000/);
  assert.match(unit, /--ozone-platform=wayland/);
  assert.match(unit, /labwc|Wayland/);
  assert.doesNotMatch(unit, /Environment=DISPLAY=/);
  assert.match(unit, /127\.0\.0\.1:8787/);
  const readme = await readFile(join(REPO, "README.md"), "utf8");
  assert.match(readme, /git pull/);
  assert.match(readme, /systemctl restart ai-studio\.service/);
  assert.match(readme, /systemctl restart chromium-kiosk\.service/);
  assert.match(readme, /ozone-platform=wayland/);
});
