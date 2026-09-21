# raspberry-pi-fun

AI Studio — Stage 1 **fishbowl** (self-running office on a Raspberry Pi).

The studio on screen is **Meridian Desk**. The stage name stays fishbowl.

## Locked defaults (2026-09-20)

- Private
- 4 employees (office manager included)
- Starting product: useful browser tool (not a game)
- Daily API ceiling: $5
- **DRY_RUN=true** unless you set `DRY_RUN=false` and provide a key

## Stack

Node orchestrator · event log · 2D canvas office · websocket · Chromium kiosk · OpenRouter

Stage 1 is in this repo. The full spec stays with the CTO handoff (`AI studio — design spec`). Out of scope here: audience site, economy enforcement, stream, hiring interviews, isometric, WebGL.

## What you get

One Node process is the world clock. Every 90–120 seconds it picks one employee, assembles a small context, makes at most one model call, runs a handful of tools, and appends events.

The display is a workplace aquarium (not a dashboard): warm dark slate, amber lamps, one accent per person. Sound stays optional — readable with it off.

| Zone | What you see |
| --- | --- |
| Top HUD | **Day N** · headcount · live model chips · burn vs **$5** bar · current task · “shipping when green” · English ticker (last 5 events) |
| Left ~55% | Event-driven top-down office. Walk, bubble, and glow only when an event happens. Failures flash red here. |
| Right ~45% | Last **green** `dist/` iframe (`LIVE · green build`). Never black. Open-in-new-tab for Connect. |
| Bottom | Who is acting + tool name (`write_file`, `say`, …) |

A stranger should, after five minutes, name the people, Timezone Buddy, whether we are under $5, and whether they would leave it on.

The office is a dollhouse of `office.json`: walls, plank floors, amber lamps, desks with monitor/mug/plant, break room, meeting table, coffee, couch, whiteboard with the ship line. Sprites walk the A* path to a real object — never abstractly “thinking.” `say()` is a tailed bubble; the ticker is English. Objects advertise (plan / hang out / break / review). Click a person or the board to inspect. Relationship scores move on reject and on a green ship.

Four load-bearing rules, unchanged from the spec:

1. **The event log is the truth.** A sprite walks because an event happened. The office is `data/events.jsonl` plus `office.json`, not a screensaver.
2. **Never show a broken build.** The product pane never points at the working copy.
3. **Bots edit data, never machinery.** Employees may write product HTML, office layout, backlog, strategy, journals, relationships. They cannot write `src/`, `public/`, config, or secrets.
4. **Secrets stay in the environment.** `OPENROUTER_API_KEY` lives under `~/.secrets/fishbowl/` and is loaded by systemd `EnvironmentFile`. Never in `workspace/`.

## Cast

| ID | Name | Role | Accent | Model |
| --- | --- | --- | --- | --- |
| `nova` | Nova Chen | programmer | coral `#F97316` | `qwen/qwen3-coder-next` |
| `kessler` | Kessler Holt | QA (+ say/journal color) | teal `#14B8A6` | `nousresearch/hermes-3-llama-3.1-70b` |
| `mira` | Mira Sol | producer | amber `#F59E0B` | `z-ai/glm-5.3-flash` |
| `jules` | Jules Park | office manager | indigo `#6366F1` | `meta-llama/llama-4-scout` |

Looks live in `workspace/employees/<id>.json`: skin, hair, outfit layers, desk_style, wardrobe_unlocked from `wardrobe-vocab.json`. Only that person may `edit_self_aesthetics`. Jules alone may `edit_office`.

Model ids are locked in `studio.config.json` (Research IDs mapped onto the product cast — there is no `river` seat). Stage 1 lottery: Nova **0.5** · Mira **0.25** · Kessler **0.25**. Jules keeps a small office-manager weight (**0.15**). Families stay distinct: **Qwen · Z.ai · Nous** (Jules is a fourth Meta seat). Tick mid is **105s** (90–120). Strongest model is used only for programmer writes; chatter seats stay on their cheap/mid ids. The HUD and hover chip show the live model id per employee. Constitution is sent as a separate cached prefix when OpenRouter honors `cache_control`. Preferred lists stay as first-available fallback after the locked ids.

Relationship stub in `workspace/relationships.json`: Nova↔Kessler −1, Mira↔Nova +1, Mira↔Kessler 0, Jules↔Nova −1, Jules↔Kessler +1, Jules↔Mira 0. Scores decay toward 0 each UTC day. Turns see the last opinions. Prompts treat them as reasonable professionals with conflicting priorities — nobody is told to be competitive.

Starting product: **Timezone Buddy**, a single-file “paste a time + city → 3–5 saved cities” converter in `workspace/product/index.html`. Whiteboard: `SHIP: Timezone Buddy — usable in <1 min`.

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

When the UTC-day spend hits **$5**, ticks stop, the HUD shows **studio sleeping**, lights dim, and the canvas replays the log.

## Run on a Raspberry Pi

Target: Raspberry Pi OS Debian **aarch64**, ~1GB RAM (Pi 3 ≈ 905MiB). Chromium kiosk is tight. Put the checkout on a **USB SSD** if you can — the event log is a lot of small writes for SD flash. Heatsink and fan; this is a 24/7 process.

### Memory tips (Pi 3)

- Enable **zram** (`sudo apt install -y zram-tools` or `dphys-swapfile` only as a last resort).
- Run **one** Chromium, kiosk only — no extra tabs, no GPU compositor.
- Office renderer forces **DPR=1** and throttles `requestAnimationFrame` when nobody is walking or speaking.
- If the compositor still swaps: `STUDIO_LITE=1` or open `/?lite=1` to disable ticker / ON AIR animations. Soundtrack stays **off** until you toggle it.

### Node v20.20.2 linux-arm64 tarball (not NodeSource 22)

Official Node **20 LTS** tarball into `$HOME/.local/node-v20.20.2`. No nvm. No NodeSource apt. Confirm **64-bit** OS first (`uname -m` → `aarch64`). If you see `armv7l`, use `linux-armv7l` instead — do not force arm64.

```bash
uname -m   # expect aarch64

NODE_VER=v20.20.2
ARCH=linux-arm64
PREFIX="$HOME/.local/node-$NODE_VER"

mkdir -p "$HOME/.local/src" "$PREFIX"
cd "$HOME/.local/src"

# Verify checksum from https://nodejs.org/dist/$NODE_VER/SHASUMS256.txt before extract in production
curl -fsSLO "https://nodejs.org/dist/${NODE_VER}/node-${NODE_VER}-${ARCH}.tar.xz"
tar -xJf "node-${NODE_VER}-${ARCH}.tar.xz" -C "$PREFIX" --strip-components=1

# User PATH (add to ~/.profile)
grep -q "node-${NODE_VER}" ~/.profile 2>/dev/null || echo "export PATH=\"$PREFIX/bin:\$PATH\"" >> ~/.profile
export PATH="$PREFIX/bin:$PATH"

node -v    # expect v20.20.2
npm -v
which node # .../.local/node-v20.20.2/bin/node
```

Optional symlink so systemd can see `/usr/local/bin/node`:

```bash
sudo ln -sfn "$HOME/.local/node-v20.20.2/bin/node" /usr/local/bin/node
sudo ln -sfn "$HOME/.local/node-v20.20.2/bin/npm"  /usr/local/bin/npm
```

`deploy/start.sh` does **not** require that symlink. It tries `$HOME/.local/node-v20.20.2` then `~/.local/node` then PATH. Do not run `npm` as root.

Smoke:

```bash
cd ~/raspberry-pi-fun   # or ~/ai-studio-fishbowl
node -e "console.log('ok', process.version, process.arch)"
# expect: ok v20.20.2 arm64
```

```bash
git clone https://github.com/akashnaren/raspberry-pi-fun.git
cd raspberry-pi-fun
export PATH="$HOME/.local/node-v20.20.2/bin:$PATH"
npm install
# DRY_RUN=true is the default. No API key. No OpenRouter calls.
npm start
```

First boot needs **no API key**. Dry-run: scripted turns, real events, real walks, $0 spent. Replay for visitors:

```bash
STUDIO_MODE=replay npm start
```

Open `http://127.0.0.1:8787` (or point Chromium kiosk at it).

### Secrets (`~/.secrets/fishbowl/` + systemd EnvironmentFile)

Secrets **never** enter `workspace/` (bots can `read_file` there). App code reads `process.env.OPENROUTER_API_KEY` only. Never log the value.

```text
~/.secrets/fishbowl/
  OPENROUTER_API_KEY     # single line, mode 0600
  openrouter.env         # KEY=value for systemd EnvironmentFile, mode 0600
```

Create once (Connect remote shell or SSH):

```bash
mkdir -p ~/.secrets/fishbowl
chmod 700 ~/.secrets/fishbowl
install -m 600 /dev/null ~/.secrets/fishbowl/OPENROUTER_API_KEY
nano ~/.secrets/fishbowl/OPENROUTER_API_KEY   # one line: sk-or-...
```

Generate `openrouter.env` without echoing the key:

```bash
umask 077
printf 'OPENROUTER_API_KEY=%s\nOPENROUTER_BASE_URL=https://openrouter.ai/api/v1\nFISHBOWL_DAILY_CEILING_USD=5\n' \
  "$(tr -d '\n' < ~/.secrets/fishbowl/OPENROUTER_API_KEY)" \
  > ~/.secrets/fishbowl/openrouter.env
chmod 600 ~/.secrets/fishbowl/openrouter.env
```

`npm start` and the systemd unit load optional EnvironmentFile-style secrets (missing files are fine — leading `-`):

1. `raspberry-pi-fun/.env` (gitignored; laptop / Pi dry-run)
2. `~/.secrets/fishbowl/openrouter.env` (live key later; never required to start)

The unit is `EnvironmentFile=-/home/pi/.secrets/fishbowl/openrouter.env`. Do not store the key in the clone, git, PR, or chat.

Live mode (only when you intend to spend): set `DRY_RUN=false` in that same env file. Four people call OpenRouter, each on a locked model id. The HUD shows **ON AIR** when live.

### Connect + LAN SSH (key-only)

| Channel | Role |
| --- | --- |
| **Raspberry Pi Connect** (screen + remote shell) | Human break-glass, kiosk eyeball, bootstrap until SSH keys land. |
| **SSH (LAN, key-only)** | Primary unattended path: apt, Node install, git, systemd, logs. |
| **rsync over SSH** | Later: push `dist/`, pull logs. Prefer git for source. Never rsync `~/.secrets/`. |
| **VNC / Samba / NFS** | Skip Week 1. |

Bring-up (Connect shell first):

```bash
loginctl enable-linger
rpi-connect status
sudo systemctl enable --now ssh
hostname -I
```

Client key (once):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_pi3 -C "fishbowl-pi3"
ssh-copy-id -i ~/.ssh/id_ed25519_pi3.pub <user>@<pi-ip>
# then on the Pi: PasswordAuthentication no; PermitRootLogin no; restart ssh
```

```sshconfig
Host pi3
  HostName <lan-ip-or-pi3.local>
  User <pi-user>
  IdentityFile ~/.ssh/id_ed25519_pi3
  IdentitiesOnly yes
```

Avoid Connect screen-share automation loops. Replay mode is the $0 demo on the HDMI TV.

### Env vars

| Variable | Default | What it does |
| --- | --- | --- |
| `DRY_RUN` | `true` | Live only when `false` **and** a key is set. |
| `STUDIO_MODE` | empty | `replay` = event log only, zero tokens. |
| `OPENROUTER_API_KEY` | empty | Live turns. Empty = dry-run. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter root. |
| `FISHBOWL_DAILY_CEILING_USD` | `5` | Hard pause when UTC-day spend hits this. HUD: studio sleeping. |
| `DAILY_CEILING_USD` | `5` | Alias for the same ceiling. |
| `STUDIO_LOCAL_STUBS` | `true` | Template `say()` when the ceiling hits. |
| `STUDIO_LITE` | empty | Disable CSS animations for Pi 3. |
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

The orchestrator records each turn's estimated USD cost in `data/spend.json`. When the UTC day hits `$5` (`FISHBOWL_DAILY_CEILING_USD` / `DAILY_CEILING_USD`), ticks stop, the burn bar fills, the lights dim, and the HUD says **studio sleeping**. The next UTC day resets the counter. This protects the card; it is independent of any later credit fiction.

### Chromium kiosk + systemd

Units live in `deploy/ai-studio.service` and `deploy/chromium-kiosk.service`. Keep them. `deploy/start.sh` resolves Node from `$HOME/.local/node-v20.20.2` then the `~/.local/node` symlink. `/usr/local` is not required. The kiosk unit only launches Chromium.

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
