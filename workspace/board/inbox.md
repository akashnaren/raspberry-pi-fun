# Board inbox — Akash 2026-09-21

## Product pivot
Flagship: **Meridian Office** — a lightweight Microsoft Office–style competitor in the browser.
- Docs / Sheets / Slides as small self-contained modules (single-file or tiny bundles)
- No signup, no payments, no file-upload-to-cloud, no chat (spec permanent out-of-scope)
- Local-only / downloadable files OK; paste-and-go UX
Timezone Buddy stays in the catalogue as a shipped utility; weekly ships can still be boring useful tools.

## More useful products (backlog seeds)
- Paste-to-CSV cleaner / column splitter
- Invoice / estimate PDF-ish HTML printer
- Meeting notes → action items extractor (local)
- Unit/currency/timezone pack (Timezone Buddy grows)
- Resume/one-pager layout
- Simple kanban (localStorage only)
- Diff / merge text
- Markdown → clean print page

## UI / office feedback (must fix)
1. **Camera fixed** — office must not pan/scroll/jitter; locked framing of the room
2. **Livelier cast** — walk between desks/break room/whiteboard on real events; speech bubbles; idle at objects (Sims rule: never abstractly “thinking”)
3. **Less AI-slop GUI** — study Apple, Tesla, Kalshi restraint: typography, whitespace, one accent, no neon gradients, no generic “AI dashboard” chrome
4. **Human writing** — product copy and say()/journals read like people, not corporate LLM
5. **HUD** — no ticker crawl, no MERIDIAN DESK wordmark, no ON AIR pills. Thin bar: day · people · $x / $5 · who’s acting. Office canvas is the hero.

## HDMI / kiosk (2026-09-21 night)
- Product pane must be Meridian Office Docs, not Timezone Buddy (light empty iframe read as a blank screen).
- Chromium on labwc needs `WAYLAND_DISPLAY=wayland-0`, `XDG_RUNTIME_DIR=/run/user/1000`, `--ozone-platform=wayland`.
- `events.jsonl` must skip/quarantine null-byte and torn lines after a power cut.
- Office canvas must paint a room before the websocket — never wait on a socket to show floor, desks, people.

## Overnight (2026-09-21)
- Dry-run beats should walk and talk: coffee, whiteboard, couch, meeting table. Named say() pulls a second person over.
- Jules must actually `edit_office` in dry-run. Small layout tweaks persist in `office.json`. Furniture budget stays two unless she adds a prop.
- Docs should read as a small editor (page, four format keys, kept locally). Sheets is a real local grid. Slides is a short deck.
- Chromium stays Pi-3 safe. No new npm deps. No OpenRouter top-up.

## Overnight (2026-09-21 late)
- Sheets: editable grid, Tab/Enter, paste CSV/TSV from the selected cell, localStorage, download `.csv`.
- Slides: 2–3 editable cards, add/remove, arrows, localStorage, download `.md` / `.html`.
- Dry-run: Nova ships the grid; Kessler bugs a one-cell paste; Mira cuts formulas; Jules tidies.
- Office canvas: stronger window light, desk lamps, distinct clutter, readable board.

## Constraints
DRY_RUN until greenlight · ~$10 OpenRouter · never top up · $5/day if live · Jules exclusive edit_office · event log is truth
