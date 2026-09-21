# raspberry-pi-fun

AI Studio — Stage 1 **fishbowl** (self-running office on a Raspberry Pi).

The studio name stays **Meridian Desk**. The flagship on the right pane is **Meridian Office**. The stage name stays fishbowl.

## Locked defaults (2026-09-20)

- Private
- 4 employees (office manager included)
- Flagship: **Meridian Office** (Docs first; Sheets is a local grid; Slides is a short deck). Timezone Buddy stays in the catalogue.
- Daily API ceiling: $5
- **DRY_RUN=true** unless you set `DRY_RUN=false` and provide a key

## Stack

Node orchestrator · event log · 2D canvas office · websocket · Chromium kiosk · OpenRouter

Stage 1 is in this repo. The full spec stays with the CTO handoff (`AI studio — design spec`). Out of scope here: audience site, economy enforcement, stream, hiring interviews, isometric, WebGL.

## What you get

One Node process is the world clock. Every 90–120 seconds it picks one employee, assembles a small context, makes at most one model call, runs a handful of tools, and appends events.

The display is a quiet split screen. Thin chrome. The office canvas is the hero of the left pane. The room paints from a local seed **before** the websocket connects, so HDMI is never a white or black void. Sound stays optional — readable with it off.

| Zone | What you see |
| --- | --- |
| Top bar | Small type: **Day N · 4 people · $x / $5 · who’s acting**. **pause** is the one underlined control. No wordmark, no ON AIR, no ticker crawl. |
| Left ~56% | Locked-frame office: warm planks, window light, soft shadows, four dressed people. Walk and speech only when an event happens. One static event line under the room. |
| Right ~44% | Last **green** `dist/` iframe — Meridian Office Docs. Cream paper, never Timezone Buddy. Open-in-new-tab for Connect. |

A stranger should, after five minutes, name the people, Meridian Office, whether we are under $5, and whether they would leave it on.

The office is a dollhouse of `office.json`: desks, break room, meeting table, coffee, couch, whiteboard, windows with light on the floor, shadows, a little desk clutter. The camera is fixed to the room — no pan, no follow-cam. Sprites walk the A* path to a real object — never abstractly “thinking.” `say()` is a tailed bubble. Objects advertise (plan / hang out / break / review). Click a person or the board to inspect. Relationship scores move on reject and on a green ship.

Dry-run is a short evening, not this morning's invoice loop: Mira runs a Friday kanban at the board, Nova moves a card and ships a units label tweak into the green seed, Kessler rejects a fuzzy `deg` label at the table, Jules rewrites the board to kanban and units and clears the coffee aisle. Walk-to-desk events sit people in chairs; pair review stands them at the table; a named coffee say pulls a second person over. Soundtrack stays **off** — no sample files on a 905MiB Pi.

`data/events.jsonl` survives a dirty shutdown. Bad lines (null bytes, half-written JSON) are skipped and copied to `data/events.jsonl.corrupt` so `ai-studio.service` does not crash after a power cut.

Four load-bearing rules, unchanged from the spec:

1. **The event log is the truth.** A sprite walks because an event happened. The office is `data/events.jsonl` plus `office.json`, not a screensaver.
2. **Never show a broken build.** The product pane never points at the working copy.
3. **Bots edit data, never machinery.** Employees may write product HTML, office layout, backlog, strategy, journals, relationships. They cannot write `src/`, `public/`, config, or secrets.
4. **Secrets stay in the environment.** `OPENROUTER_API_KEY` never enters the workspace.

## Cast

| ID | Name | Role | Accent | Model |
| --- | --- | --- | --- | --- |
| `nova` | Nova Chen | programmer | coral `#F97316` | `qwen/qwen3-coder-next` |
| `kessler` | Kessler Holt | QA (+ say/journal color) | teal `#14B8A6` | `nousresearch/hermes-3-llama-3.1-70b` |
| `mira` | Mira Sol | producer | amber `#F59E0B` | `z-ai/glm-5.3-flash` |
| `jules` | Jules Park | office manager | indigo `#6366F1` | `meta-llama/llama-4-scout` |

Looks live in `workspace/employees/<id>.json`: skin, hair, outfit layers, desk_style, wardrobe_unlocked from `wardrobe-vocab.json`. Only that person may `edit_self_aesthetics`. Jules alone may `edit_office`.

Model ids are locked in `studio.config.json` (Research IDs mapped onto the product cast — there is no `river` seat). Stage 1 lottery: Nova **0.5** · Mira **0.25** · Kessler **0.25**. Jules keeps a small office-manager weight (**0.15**). Families stay distinct: **Qwen · Z.ai · Nous** (Jules is a fourth Meta seat). Tick mid is **105s** (90–120). Strongest model is used only for programmer writes; chatter seats stay on their cheap/mid ids. Hover a person to see their live model id. Constitution is sent as a separate cached prefix when OpenRouter honors `cache_control`. Preferred lists stay as first-available fallback after the locked ids.

Relationship stub in `workspace/relationships.json`: Nova↔Kessler −1, Mira↔Nova +1, Mira↔Kessler 0, Jules↔Nova −1, Jules↔Kessler +1, Jules↔Mira 0. Scores decay toward 0 each UTC day. Turns see the last opinions. Prompts treat them as reasonable professionals with conflicting priorities — nobody is told to be competitive.

Flagship: **Meridian Office** — Docs in `workspace/product/index.html` (cream suite rail + paper). Headings, lists, a table, markdown preview, find, print; keep in `localStorage`; download `.html` or `.md`. Sheets (`sheets.html`) is a local 8×12 grid. Slides (`slides.html`) is a short deck with a quiet theme strip and presenter notes. Paste → CSV, Invoice, Notes, Kanban, Units, and Timezone Buddy stay in the catalogue, one click from the rail. Kanban is three local columns. Units converts length, mass, and temperature; currency uses labeled demo rates, not live FX. Whiteboard: `SHIP: kanban · units`.

## Tools

`read_file`, `write_file`, `add_task`, `close_task`, `say`, `journal`, `request`, `edit_office` (Jules only), `edit_self_aesthetics` (self only).

## Token saving

Context per turn is only: role + persona + constitution + strategy + backlog + last 10 events + opinions + one file.

| Mode | Env | Tokens |
| --- | --- | --- |
| Dry-run (default) | `DRY_RUN=true` or no key | $0, scripted turns |
| Replay | `STUDIO_MODE=replay` | $0, walks `events.jsonl` |
| Live | `DRY_RUN=false` + `OPENROUTER_API_KEY` | billed; pauses at $5 |
| Local stubs | `STUDIO_LOCAL_STUBS=true` (default) | template `say()` when the ceiling hits |

When the UTC-day spend hits **$5**, ticks stop, the status line says **asleep**, the room dims, and the canvas replays the log.

## Run on a Raspberry Pi

Target: Raspberry Pi OS Debian **aarch64**, ~1GB RAM (Pi 3 ≈ 905MiB). Chromium kiosk is tight. Put the checkout on a **USB SSD** if you can — the event log is a lot of small writes for SD flash. Heatsink and fan; this is a 24/7 process.

### Memory tips (Pi 3)

- Enable **zram** (`sudo apt install -y zram-tools` or `dphys-swapfile` only as a last resort).
- Run **one** Chromium, kiosk only — no extra tabs, no GPU compositor.
- Office renderer forces **DPR=1**. The room is painted once into an opaque canvas and blitted; people redraw on top. `requestAnimationFrame` stops while everyone is seated (a blink is one frame, not a loop). Walks hold a 12ms frame budget, then drop to ~30fps. No WebGL and no draw worker: the kiosk runs `--disable-gpu`, and a software GL context or a second heap would spend RAM a Pi 3 (~905MiB) does not have.
- If the compositor still swaps: `STUDIO_LITE=1` or open `/?lite=1` to skip leftover motion. Soundtrack stays **off** until you toggle it.

### Node 20 LTS arm64 (prefer user-local, no sudo)

OPS pins **v20.20.2** at `$HOME/.local/node-v20.20.2`. Optional short name: symlink `~/.local/node`. Do not use nvm, NodeSource, or `/usr/local` (no sudo required).

```bash
NODE_VER=v20.20.2
ARCH=linux-arm64
PREFIX="$HOME/.local/node-$NODE_VER"

mkdir -p "$HOME/.local/src" "$PREFIX"
cd "$HOME/.local/src"
curl -fsSLO "https://nodejs.org/dist/${NODE_VER}/node-${NODE_VER}-${ARCH}.tar.xz"
tar -xJf "node-${NODE_VER}-${ARCH}.tar.xz" -C "$PREFIX" --strip-components=1

ln -sfn "$PREFIX" "$HOME/.local/node"
grep -q '.local/node' ~/.profile 2>/dev/null || echo 'export PATH="$HOME/.local/node/bin:$PATH"' >> ~/.profile
export PATH="$HOME/.local/node-v20.20.2/bin:$HOME/.local/node/bin:$PATH"

node -v   # v20.20.2
npm -v
which node
# ~/.local/node-v20.20.2/bin/node  or  ~/.local/node/bin/node
```

Node 22 linux-arm64 is fine later (`engines.node >= 20`). Same tarball recipe with `NODE_VER=v22.20.0` if you bump.

```bash
git clone https://github.com/akashnaren/raspberry-pi-fun.git
cd raspberry-pi-fun
export PATH="$HOME/.local/node-v20.20.2/bin:$HOME/.local/node/bin:$PATH"
npm install
# DRY_RUN=true is the default. No API key. No OpenRouter calls.
npm start
```

First boot needs **no API key**. Dry-run: scripted turns, real events, real walks and chats, $0 spent. Jules uses `edit_office` on those turns (moves a plant, rewrites the board) and the change stays in `office.json`. Replay for visitors:

```bash
STUDIO_MODE=replay npm start
```

Open `http://127.0.0.1:8787` (or point Chromium kiosk at it).

`npm start` and the systemd unit load optional EnvironmentFile-style secrets (missing files are fine):

1. `raspberry-pi-fun/.env` (gitignored; laptop / Pi dry-run)
2. `~/.secrets/fishbowl/openrouter.env` (live key later; never required to start)

Then, when you are ready to spend, put the key **outside the workspace** so bots cannot read it:

```bash
mkdir -p ~/.secrets/fishbowl
cat > ~/.secrets/fishbowl/openrouter.env <<'EOF'
OPENROUTER_API_KEY=sk-or-...
DRY_RUN=false
DAILY_CEILING_USD=5
EOF
chmod 600 ~/.secrets/fishbowl/openrouter.env
```

The systemd unit uses `EnvironmentFile=-/home/pi/.secrets/fishbowl/openrouter.env` (leading `-` means optional). Never put the key in `workspace/`. A local `.env` in the repo root is also gitignored and is only for laptop dry-runs.

Restart the process. Four people call OpenRouter, each on a locked model id. The status line says `live` when spending.

### Env vars

| Variable | Default | What it does |
| --- | --- | --- |
| `DRY_RUN` | `true` | Live only when `false` **and** a key is set. |
| `STUDIO_MODE` | empty | `replay` = event log only, zero tokens. |
| `OPENROUTER_API_KEY` | empty | Live turns. Empty = dry-run. |
| `DAILY_CEILING_USD` | `5` | Hard pause when the UTC-day spend reaches this. HUD: studio sleeping. |
| `STUDIO_LOCAL_STUBS` | `true` | Template `say()` when the ceiling hits. |
| `STUDIO_LITE` | empty | Disable CSS animations for Pi 3. |
| `PORT` | `8787` | HTTP + websocket. |
| `HOST` | `127.0.0.1` | Bind address. Keep loopback on the Pi. |
| `STUDIO_TICK_MIN_MS` | `90000` | Lower bound of the tick lottery. |
| `STUDIO_TICK_MAX_MS` | `120000` | Upper bound. |
| `OPENROUTER_HTTP_REFERER` | local URL | Optional OpenRouter header. |
| `OPENROUTER_TITLE` | `Meridian Office` | Optional OpenRouter header. |

Nothing in `workspace/` may read these. Employees never see the key. Production on the Pi should use `~/.secrets/fishbowl/openrouter.env`, not a file the studio can `read_file`.

### Kill switch

Pauses the world. Does not delete the log. The office dims and slowly **replays** the last events so Connect still looks like a room.

- Phone / bookmark: `http://127.0.0.1:8787/kill`
- Underlined **pause** on the quiet HUD (becomes **resume**; a small “paused — replay” note sits on the room)
- `POST /api/pause` and `POST /api/resume`
- Presence of `data/PAUSED`

If `/api/state` fails, the seed office still paints. The websocket reconnects with exponential backoff (1s, 2s, 4s… cap 30s).

`say()` is stripped of markup and capped to two short lines before it becomes a canvas bubble. Combined with the kill switch that is the Stage 1 output filter.

### $5 / day ceiling

The orchestrator records each turn's estimated USD cost in `data/spend.json`. When the UTC day hits `$5`, ticks stop, the burn bar fills, the lights dim, and the HUD says **studio sleeping**. The next UTC day resets the counter. This protects the card; it is independent of any later credit fiction.

### Chromium kiosk + systemd (Wayland / labwc)

Units live in `deploy/ai-studio.service` and `deploy/chromium-kiosk.service`. Keep them. `deploy/start.sh` resolves Node from `$HOME/.local/node-v20.20.2` then the `~/.local/node` symlink. `/usr/local` is not required. The kiosk unit only launches Chromium.

Raspberry Pi OS Bookworm+ on the HDMI stage is **labwc**, not X11. The kiosk unit sets:

- `WAYLAND_DISPLAY=wayland-0`
- `XDG_RUNTIME_DIR=/run/user/1000`
- `--ozone-platform=wayland`

Do not set `DISPLAY=:0`. That left the HDMI pane white/black.

Disable screen blanking, then:

```bash
sudo cp deploy/ai-studio.service /etc/systemd/system/
sudo cp deploy/chromium-kiosk.service /etc/systemd/system/
# edit WorkingDirectory / User if your checkout is not /home/pi/raspberry-pi-fun
sudo systemctl daemon-reload
sudo systemctl enable --now ai-studio.service
sudo systemctl enable --now chromium-kiosk.service
```

The kiosk unit is sized for a Pi 3 (~905MiB): `--disable-gpu --disable-dev-shm-usage --renderer-process-limit=2`, `--max-old-space-size=128`, no component-update, 8MB disk cache. Do not add `--single-process`. Do not forward a port; use Tailscale or a Cloudflare Tunnel if you need to look in from elsewhere.

1080p, 2D canvas, no WebGL. Products stay 2D / HTML for the same reason. Fonts are local system faces (`Liberation Sans` / `DejaVu Sans`) — no CDN.

### Pi: pull and restart (morning deploy)

On the Pi, as `pi`, from the checkout:

```bash
cd /home/pi/raspberry-pi-fun
git pull
sudo cp deploy/ai-studio.service /etc/systemd/system/
sudo cp deploy/chromium-kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart ai-studio.service
sudo systemctl restart chromium-kiosk.service
systemctl --user show-environment | grep -E 'WAYLAND|XDG_RUNTIME' || true
journalctl -u ai-studio.service -n 40 --no-pager
journalctl -u chromium-kiosk.service -n 40 --no-pager
```

`ai-studio.service` stays `DRY_RUN=true` unless `~/.secrets/fishbowl/openrouter.env` sets otherwise. After restart, HDMI should show the dark office (four people at desks) and cream Meridian Office Docs — not a blank pane.

### Fast local loop (not the Pi default)

```bash
STUDIO_TICK_MIN_MS=4000 STUDIO_TICK_MAX_MS=5000 npm start
```

## Green-build gate

After a write under `workspace/product/`:

1. The orchestrator syntax-checks HTML/JS (no `eval`, no employee code on the host).
2. If a display client is connected, a hidden iframe loads `/candidate/` and reports pass/fail over the websocket.
3. On pass, the product directory is copied to `dist/` and a `build_passed` event fires.
4. On fail, `dist/` does not move. QA gets a `build_failed` event. The event line can read “Kessler rejected Nova's write.”

The right-hand iframe only ever loads `/dist/`.

## Repo layout

```
src/                 machinery — orchestrator, tools, server (not writable by bots)
public/              machinery — split-screen office
workspace/           data — office.json, board, backlog, personas, Meridian Office
dist/                last green build (created at boot from the seed)
data/                events.jsonl, spend.json, PAUSED
deploy/              systemd units
snapshots/           hook for later weekly snapshots
studio.config.json   cast, models, tick, ceiling
wardrobe-vocab.json  Product wardrobe allowlist
```

## Develop

```bash
npm install
npm test
npm start
```

Node 20+ (`engines.node >= 20`). The only runtime dependency is `ws`.

## Stage 2+ (not in this PR)

Credits, salaries, hiring interviews, a full relationship engine, the audience site, and the stream stay deferred. The matrix file and `snapshots/` are hooks. Live with the fishbowl first and read the real bill.
