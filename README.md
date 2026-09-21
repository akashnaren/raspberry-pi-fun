# raspberry-pi-fun

AI Studio — Stage 1 **fishbowl** (self-running office on a Raspberry Pi).

The studio on screen is **Meridian Desk**. The stage name stays fishbowl.

## Locked defaults (2026-09-20)

- Private
- 3 employees originally; Stage 1 now seats **4** (office manager added)
- Starting product: useful browser tool (not a game)
- Daily API ceiling: $5

## Stack

Node orchestrator · event log · 2D canvas office · websocket · Chromium kiosk · OpenRouter

Stage 1 is in this repo. The full spec stays with the CTO handoff (`AI studio — design spec`). Out of scope here: audience site, economy enforcement, stream, hiring interviews, isometric, WebGL.

## What you get

One Node process is the world clock. Every 90–120 seconds it picks one employee, assembles a small context, makes at most one model call, runs a handful of tools, and appends events.

The display is one page, split down the middle:

| Left | Right |
| --- | --- |
| Top-down office. Pixel people at desks and objects, driven by the event log plus `workspace/office.json`. | An iframe pointed at `dist/` — the last build that passed the green-build gate. |

Four load-bearing rules, unchanged from the spec:

1. **The event log is the truth.** A sprite walks because an event happened. The office is `data/events.jsonl` plus `office.json`, not a screensaver.
2. **Never show a broken build.** The product pane never points at the working copy.
3. **Bots edit data, never machinery.** Employees may write product HTML, office layout, backlog, strategy, journals, relationships. They cannot write `src/`, `public/`, config, or secrets.
4. **Secrets stay in the environment.** `OPENROUTER_API_KEY` never enters the workspace.

## Cast

| ID | Name | Role | Accent | Model family | Priority |
| --- | --- | --- | --- | --- | --- |
| `nova` | Nova Chen | programmer | coral `#F97316` | OpenAI | Ship a usable tool tonight. Working > pretty. |
| `kessler` | Kessler Holt | QA | teal `#14B8A6` | Nous / Hermes | No ugly or broken UX. Reject loose greens. |
| `mira` | Mira Sol | producer | amber `#F59E0B` | xAI / Grok | One clear useful tool. Kill scope creep. |
| `reed` | Reed Park | office manager | violet `#8B5CF6` | Google | Room stays usable. Furniture budget is real. |

Looks live in `workspace/employees/<id>.json`: skin, hair, outfit layers, desk_style, wardrobe_unlocked. Only that person may `edit_self_aesthetics`. Reed alone may `edit_office`.

Model ids are not hardcoded in the orchestrator. On a live boot the process reads OpenRouter's model list and picks each employee's first available `preferredModels` entry (then any model in that family). Edit `studio.config.json` to swap brains. Lottery weights: Nova 4, everyone else 2.

Relationship stub in `workspace/relationships.json`: Nova↔Kessler −1, Mira↔Nova +1, Mira↔Kessler 0, plus Reed pairs. Scores decay toward 0 each UTC day. Turns see the last opinions. Prompts treat them as reasonable professionals with conflicting priorities — nobody is told to be competitive.

Starting product: **Timezone Buddy**, a single-file “paste a time + city → 3–5 saved cities” converter in `workspace/product/index.html`. Whiteboard: `SHIP: Timezone Buddy — usable in <1 min`.

## Tools

`read_file`, `write_file`, `add_task`, `close_task`, `say`, `journal`, `request`, `edit_office` (Reed only), `edit_self_aesthetics` (self only).

## Run on a Raspberry Pi

Use Raspberry Pi OS Bookworm 64-bit. Put the checkout on a **USB SSD** if you can — the event log is a lot of small writes for SD flash. Heatsink and fan; this is a 24/7 process.

Install **Node 20 LTS** from the official linux-arm64 tarball into `/usr/local`. No nvm. No NodeSource apt repo.

```bash
sudo apt update
sudo apt install -y curl xz-utils git

NODE_VER=20.19.5
curl -fsSL "https://nodejs.org/dist/v${NODE_VER}/node-v${NODE_VER}-linux-arm64.tar.xz" -o /tmp/node.tar.xz
sudo tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
node -v   # v20.19.x
npm -v

git clone https://github.com/akashnaren/raspberry-pi-fun.git
cd raspberry-pi-fun
npm install
cp .env.example .env
npm start
```

On a Pi 3 (~905MiB) that is enough. The office renderer forces **DPR=1** and throttles `requestAnimationFrame` when nobody is walking or speaking.

First boot needs **no API key**. Without `OPENROUTER_API_KEY` the studio runs in **dry-run**: scripted turns, real events, real walks, $0 spent. Open `http://127.0.0.1:8787` (or point Chromium kiosk at it).

Then, when you are ready to spend, put the key **outside the workspace** so bots cannot read it:

```bash
mkdir -p ~/.secrets/fishbowl
cat > ~/.secrets/fishbowl/openrouter.env <<'EOF'
OPENROUTER_API_KEY=sk-or-...
DAILY_CEILING_USD=5
EOF
chmod 600 ~/.secrets/fishbowl/openrouter.env
```

The systemd unit loads `EnvironmentFile=-%h/.secrets/fishbowl/openrouter.env`. Never put the key in `workspace/`. A local `.env` in the repo root is also gitignored and is only for laptop dry-runs.

Restart the process. Four people call OpenRouter, each on a different model family. The HUD shows **ON AIR** when live.

### Env vars

| Variable | Default | What it does |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | empty | Live turns. Empty = dry-run. |
| `DAILY_CEILING_USD` | `5` | Hard pause when the UTC-day spend reaches this. Lights dim; ticker says so. |
| `PORT` | `8787` | HTTP + websocket. |
| `HOST` | `127.0.0.1` | Bind address. Keep loopback on the Pi. |
| `STUDIO_TICK_MIN_MS` | `90000` | Lower bound of the tick lottery. |
| `STUDIO_TICK_MAX_MS` | `120000` | Upper bound. |
| `OPENROUTER_HTTP_REFERER` | local URL | Optional OpenRouter header. |
| `OPENROUTER_TITLE` | `Meridian Desk` | Optional OpenRouter header. |

Nothing in `workspace/` may read these. Employees never see the key. Production on the Pi should use `~/.secrets/fishbowl/openrouter.env`, not a file the studio can `read_file`.

### Kill switch

Pauses the world. Does not delete the log. The office dims and slowly **replays** the last events so Connect still looks like a room.

- Phone / bookmark: `http://127.0.0.1:8787/kill`
- Button on the HUD
- `POST /api/pause` and `POST /api/resume`
- Presence of `data/PAUSED`

`say()` is stripped of markup and capped to two short lines before it becomes a canvas bubble. Combined with the kill switch that is the Stage 1 output filter.

### $5 / day ceiling

The orchestrator records each turn's estimated USD cost in `data/spend.json`. When the UTC day hits `$5`, ticks stop, the burn bar fills, the lights dim, and the ticker says the ceiling hit. The next UTC day resets the counter. This protects the card; it is independent of any later credit fiction.

### Chromium kiosk + systemd

Disable screen blanking, then:

```bash
sudo cp deploy/ai-studio.service /etc/systemd/system/
sudo cp deploy/chromium-kiosk.service /etc/systemd/system/
# edit WorkingDirectory / User if your checkout is not /home/pi/raspberry-pi-fun
sudo systemctl daemon-reload
sudo systemctl enable --now ai-studio.service
sudo systemctl enable --now chromium-kiosk.service
```

The kiosk unit is sized for a Pi 3: `--disable-gpu --disable-dev-shm-usage --renderer-process-limit=2` and a small V8 heap. Do not forward a port; use Tailscale or a Cloudflare Tunnel if you need to look in from elsewhere.

1080p, 2D canvas, no WebGL. Products stay 2D / HTML for the same reason. Fonts are local system faces (`Liberation Sans` / `DejaVu Sans`) — no CDN.

### Fast local loop (not the Pi default)

```bash
STUDIO_TICK_MIN_MS=4000 STUDIO_TICK_MAX_MS=5000 npm start
```

## Green-build gate

After a write under `workspace/product/`:

1. The orchestrator syntax-checks HTML/JS (no `eval`, no employee code on the host).
2. If a display client is connected, a hidden iframe loads `/candidate/` and reports pass/fail over the websocket.
3. On pass, the product directory is copied to `dist/` and a `build_passed` event fires.
4. On fail, `dist/` does not move. QA gets a `build_failed` event. The ticker can read “Kessler rejected Nova's write.”

The right-hand iframe only ever loads `/dist/`.

## Repo layout

```
src/                 machinery — orchestrator, tools, server (not writable by bots)
public/              machinery — split-screen office
workspace/           data — office.json, relationships.json, backlog, personas, Timezone Buddy
dist/                last green build (created at boot from the seed)
data/                events.jsonl, spend.json, PAUSED
deploy/              systemd units
snapshots/           hook for later weekly snapshots
studio.config.json   cast, families, tick, ceiling
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
