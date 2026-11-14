# OpenPi

I built a small chat system that runs on three Raspberry Pis in a 3D-printed rack. I wanted something I could actually use every day, at home, on my own hardware, without sending the conversation to a hosted model.

The public chat is at https://rpi-pi4.taild6c87d.ts.net/

The boards run stdlib Python, with no pip. The page is served on port 18080.

## Why I built it

I wanted to find out if a real chat product could live on a cheap, low-power rack I designed and printed myself. Not a one-prompt demo. Something I would open and talk to.

## What I was aiming for

- Keep generation on one board that can actually do it, and only there
- Put search, documents, and memory on the smaller boards so the brain stays free
- Answer repeated questions from a local map, without running the model again
- Keep the surface simple: a web chat, and an API other tools can call

## How I got here

I started with three boards in one custom rack, on a Tailscale network. The hostnames are rpi-pi2, rpi-pi3, and rpi-pi4.

The hard part was that the boards are not interchangeable. pi2 has about 1GB of RAM and an armv7 userland, and Ollama does not run there. pi3 also has about 1GB; a small model can load, but generation is not reliable. pi4 has about 8GB, and it is the only board that has produced usable replies.

So I stopped treating them as copies of each other. pi4 is the only decoder. pi3 holds the dataset, OCR, documents, memory, and embeddings. pi2 stays up for health, web search, and image cards. If the local map misses and pi4 is down, the router will not invent an answer from the weaker boards.

Repeated lines go into a canned map. A hit never enters the model. A new line is generated on pi4 once, and a later job on pi3 can fold that pair into the map, then delete the raw training files. The delete step matters, because a 1GB card fills up fast.

The chat page is built in `pi-pair/web`. `npm run build` writes `pi-pair/static`.

![3D-printed vertical rack with three Raspberry Pis](docs/rack/rack-hero.jpg)

The rack I printed, with pi2, pi3, and pi4 in it.

![Front ports, USB Wi-Fi adapters, and antennas](docs/rack/rack-front.jpg)

The front of the rack, with the ports, USB Wi-Fi adapters, and antennas.

![Top view of the rack enclosure](docs/rack/rack-top.jpg)

Looking down on the enclosure.

## How it works now

| Board | Role | What it runs |
| --- | --- | --- |
| pi4 | brain | Flash `qwen3:0.6b` and Pro `qwen3:1.7b`. The only board that decodes. |
| pi3 | dataset | OCR, documents, charts, memory, compaction, Arctic xs embed. No decode. |
| pi2 | health | Web search and image cards. No decode. |

The chat page can be served from any of the three boards. Generation does not follow the page. It stays on pi4. More detail is in [Architecture](pi-pair/docs/ARCHITECTURE.md).

Search runs when the composer Search control is on. If it is off, the turn still searches when the line asks to search, look something up, use the web or tools, or google, or when it asks for the news in a short shape: latest news, news about, news on, latest on, in the news, what's the latest, or latest <word> news. A bare news, latest, current, or today, and every other line, is one local decode. The lookup stays on pi2.

The public URL is a Tailscale Funnel on pi4 to `http://127.0.0.1:18080`. From 11:00 PM to 7:00 AM America/Los_Angeles that port shows a downtime banner instead of chat. Chats held in memory are lost when `pi-pair` stops for the night, and during that window pi2 and pi3 cannot reach pi4 on port 18080.

## Using it

Open the public page above to chat. The live API docs are at https://rpi-pi4.taild6c87d.ts.net/docs, and the same surface is described in [API](pi-pair/docs/API.md).

On a board:

```bash
bash pi-pair/install.sh
```

Then run the `systemctl --user` lines it prints. The installer copies the whole `pair/` package, so a module that moved cannot linger on the board as a stale file. `peers.json` is gitignored. `install.sh` creates it from `peers.example.json` only when the board does not already have one.

To develop in this repo:

```bash
make check
git config core.hooksPath .githooks
```

`make check` runs ruff, the page typecheck, shellcheck, the Python tests, the page tests, and a rebuild check of `pi-pair/static`.

### Nightly downtime on pi4

The copies that run on the board are under `~/openpi-downtime/` and `~/.config/systemd/user/`. The tracked files are in `pi-pair/ops/downtime/`. `openpi-downtime.sh auto` turns the banner on when the Pacific hour is 23 or 0-6, so the window crosses midnight. 6:50 AM is still inside it. The same command is idempotent. It does not change `mini_chat.py`.

Install on pi4 as user `pi`, from a checkout:

```bash
mkdir -p ~/openpi-downtime ~/.config/systemd/user
cp pi-pair/ops/downtime/openpi-downtime.sh pi-pair/ops/downtime/banner_server.py pi-pair/ops/downtime/banner.html ~/openpi-downtime/
cp pi-pair/ops/downtime/openpi-banner.service pi-pair/ops/downtime/openpi-downtime-apply.service pi-pair/ops/downtime/openpi-downtime-start.timer pi-pair/ops/downtime/openpi-downtime-end.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now openpi-downtime-start.timer openpi-downtime-end.timer openpi-downtime-apply.service
```

The start timer is 23:00 America/Los_Angeles and the end timer is 07:00. Both are persistent and both start `openpi-downtime-apply.service`, which runs `auto`. That unit also runs at boot.

Disable the window and bring chat back:

```bash
systemctl --user disable --now openpi-downtime-start.timer openpi-downtime-end.timer openpi-downtime-apply.service && ~/openpi-downtime/openpi-downtime.sh up
```

## Repo layout

```
pi-pair/pair/core        config, errors, runtime, timing
pi-pair/pair/mesh        peers, health, offload, guard
pi-pair/pair/model       modes, knobs, chat, preload, sched
pi-pair/pair/turn        shape, assist, moderate, abilities
pi-pair/pair/memory      store, compact, ledger
pi-pair/pair/search      web lookup
pi-pair/pair/ingest      upload, OCR, documents
pi-pair/pair/render      charts, images, documents
pi-pair/pair/flywheel    canned map, queue, train cycle
pi-pair/pair/routes      HTTP route mixins
pi-pair/pair/nodes       worker, embedder, web search, memory store
pi-pair/pair/server.py   front server
pi-pair/web              page source; npm run build writes pi-pair/static
pi-pair/tests            unittest, mirrors pair/
pi-pair/scripts          operator commands (bench/, qa/, data/, eval/, lifecycle/, train/)
pi-pair/ops/downtime     pi4 nightly banner, 11:00 PM-7:00 AM Pacific
pi-pair/docs             how the rack is built and run
docs/rack                photos of the rack
```

## Docs

- [Architecture](pi-pair/docs/ARCHITECTURE.md): boards, mesh, the page, and what a miss does
- [API](pi-pair/docs/API.md): endpoints, the keyed API, agents, environment
- [Operations](pi-pair/docs/OPERATIONS.md): start, install, failures
- [Deploy](pi-pair/docs/DEPLOY.md): CI and the pi3 sync
- [Train](pi-pair/docs/TRAIN.md): train, then delete
- [Datasets](pi-pair/docs/DATASETS.md)
- [Embed](pi-pair/docs/EMBED.md)
- [Images](pi-pair/docs/IMAGES.md)
- [Sources](pi-pair/docs/SOURCES.md)
