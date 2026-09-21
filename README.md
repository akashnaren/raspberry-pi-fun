# raspberry-pi-fun

AI Studio — Stage 1 **fishbowl** (self-running AI office on a Raspberry Pi).

## Locked defaults (2026-09-20)

- Private
- 3 employees
- Starting product: useful browser tool (not a game)
- Daily API ceiling: $5

## Stack

Node orchestrator · event log · 2D canvas office · websocket · Chromium kiosk · OpenRouter

Stage 1 is in this repo. The full spec stays with the CTO handoff (`AI studio — design spec`). Out of scope here: audience site, economy enforcement, stream, hiring interviews.

## What you get

One Node process is the world clock. Every 90–120 seconds it picks one employee, assembles a small context, makes at most one model call, runs a handful of tools, and appends events.

The display is one page, split down the middle:

| Left | Right |
| --- | --- |
| Top-down office. Rectangles with name tags, driven by the event log plus `workspace/office.json`. | An iframe pointed at `dist/` — the last build that passed the green-build gate. |

Four load-bearing rules, unchanged from the spec:

1. **The event log is the truth.** The office is a rendering of `data/events.jsonl`, not a screensaver.
2. **Never show a broken build.** The product pane never points at the working copy.
3. **Bots edit data, never machinery.** Employees may write product HTML, office layout, backlog, strategy, journals. They cannot write `src/`, `public/`, config, or secrets.
4. **Secrets stay in the environment.** `OPENROUTER_API_KEY` never enters the workspace.

## Cast

| Name | Role | Model family (config) | Priority |
| --- | --- | --- | --- |
| Mira | producer | xAI / Grok | Tiny scope. Stamp stays a timestamp tool. |
| Nova | programmer | OpenAI | Ship a single HTML file. Iterate in the open. |
| Kessler | QA | Nous / Hermes | Use `dist/`. File overflow and timezone bugs. |

Model ids are not hardcoded in the orchestrator. On a live boot the process reads OpenRouter's model list and picks each employee's first available `preferredModels` entry (then any model in that family). Edit `studio.config.json` to swap brains.

Starting product: **Stamp**, a single-file Unix / ISO / timezone converter in `workspace/product/index.html`.

## Tools

`read_file`, `write_file`, `add_task`, `close_task`, `say`, `journal`.

## Run on a Raspberry Pi

Use Raspberry Pi OS Bookworm 64-bit. Put the checkout on a **USB SSD** if you can — the event log is a lot of small writes for SD flash. Heatsink and fan; this is a 24/7 process.

```bash
sudo apt update
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
git clone https://github.com/akashnaren/raspberry-pi-fun.git
cd raspberry-pi-fun
npm install
cp .env.example .env
npm start
```

First boot needs **no API key**. Without `OPENROUTER_API_KEY` the studio runs in **dry-run**: scripted turns, real events, real office motion, $0 spent. Open `http://127.0.0.1:8787` (or point Chromium kiosk at it).

Then, when you are ready to spend:

```bash
# in .env
OPENROUTER_API_KEY=sk-or-...
DAILY_CEILING_USD=5
```

Restart the process. Mira / Nova / Kessler will start calling OpenRouter, each on a different model family.

### Env vars

| Variable | Default | What it does |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | empty | Live turns. Empty = dry-run. |
| `DAILY_CEILING_USD` | `5` | Hard pause when the UTC-day spend reaches this. |
| `PORT` | `8787` | HTTP + websocket. |
| `HOST` | `127.0.0.1` | Bind address. Keep loopback on the Pi. |
| `STUDIO_TICK_MIN_MS` | `90000` | Lower bound of the tick lottery. |
| `STUDIO_TICK_MAX_MS` | `120000` | Upper bound. |
| `OPENROUTER_HTTP_REFERER` | local URL | Optional OpenRouter header. |
| `OPENROUTER_TITLE` | `AI Studio Fishbowl` | Optional OpenRouter header. |

Nothing in `workspace/` may read these. Employees never see the key.

### Kill switch

Pauses the world. Does not delete the log.

- Phone / bookmark: `http://127.0.0.1:8787/kill`
- Button on the HUD
- `POST /api/pause` and `POST /api/resume`
- Presence of `data/PAUSED`

`say()` is stripped of markup and capped before it becomes a bubble. Combined with the kill switch that is the Stage 1 output filter.

### $5 / day ceiling

The orchestrator records each turn's estimated USD cost (OpenRouter usage, conservative fallback rates) in `data/spend.json`. When the UTC day hits `$5`, ticks stop and the HUD shows the pause. The next UTC day resets the counter. This protects the card; it is independent of any later credit fiction.

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

Kiosk command is Chromium pointed at the local page. Do not forward a port; use Tailscale or a Cloudflare Tunnel if you need to look in from elsewhere.

1080p, 2D canvas, no WebGL. Products stay 2D / HTML for the same reason.

### Fast local loop (not the Pi default)

```bash
STUDIO_TICK_MIN_MS=4000 STUDIO_TICK_MAX_MS=5000 npm start
```

## Green-build gate

After a write under `workspace/product/`:

1. The orchestrator syntax-checks HTML/JS (no `eval`, no employee code on the host).
2. If a display client is connected, a hidden iframe loads `/candidate/` and reports pass/fail over the websocket.
3. On pass, the product directory is copied to `dist/` and a `build_passed` event fires.
4. On fail, `dist/` does not move. QA gets a `build_failed` event.

The right-hand iframe only ever loads `/dist/`.

## Repo layout

```
src/                 machinery — orchestrator, tools, server (not writable by bots)
public/              machinery — split-screen office
workspace/           data — office.json, backlog, personas, Stamp source
dist/                last green build (created at boot from the seed)
data/                events.jsonl, spend.json, PAUSED
deploy/              systemd units
studio.config.json   cast, families, tick, ceiling
```

## Develop

```bash
npm install
npm test
npm start
```

Node 20+ (22 on the Pi is fine). The only runtime dependency is `ws`.

## Stage 2+ (not in this PR)

Credits, salaries, hiring interviews, the relationship matrix, the audience site, and the stream stay deferred. Live with the fishbowl first and read the real bill.
