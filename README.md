# OpenPi

OpenPi is a chat website and small API I run on three Raspberry Pis in a rack I designed and printed at home.

I wanted daily chat on my own hardware, without sending every message to a hosted model in the cloud.

## Why I built it

I was curious whether something that felt like a real product could live on cheap boards and stay useful. I did not want a one off demo. I wanted to open it, ask a question, and get an answer back from my rack.

## How I built it

I put three Pis in one vertical enclosure and joined them with Tailscale. The names are rpi-pi2, rpi-pi3, and rpi-pi4.

The boards are not the same. pi2 has about 1 GB of RAM and cannot run the local model runtime I use. pi3 also has about 1 GB. A small model can load there, but replies were not reliable enough to trust. pi4 has about 8 GB and is the only board that produces answers I would actually send to a user.

I split the work by strength. pi4 runs text generation only. pi3 holds files, runs OCR on uploads, keeps conversation memory, and stores embeddings (numeric summaries of text used for lookup). pi2 handles health checks, web search, and image cards for replies. If pi4 is down, the system does not pretend a weaker board can replace it.

Common question and answer pairs can land in a local map on pi3. A map hit skips the model entirely. When something new is generated on pi4, a later job on pi3 can add that pair to the map and delete the raw training files so the SD card does not fill up.

![3D-printed vertical rack with three Raspberry Pis](docs/rack/rack-hero.jpg)

![Front ports, USB Wi-Fi adapters, and antennas](docs/rack/rack-front.jpg)

![Top view of the rack enclosure](docs/rack/rack-top.jpg)

The chat page is built in `pi-pair/web`. Running `npm run build` writes the files under `pi-pair/static` that the Python server serves.

## What it does today

You get a web chat, voice input, file upload with local OCR, optional web search, and a streaming API compatible with common chat client patterns. The boards run Python from the standard library only on the Pis (no pip install step on the devices).

pi4 runs two on device models: Flash uses `qwen3:0.6b` for faster replies. Pro uses `qwen3:1.7b` when you pick it or when Auto routes a harder question there. pi3 runs dataset, memory, and embed work. pi2 runs search and image cards. Any board can serve the page, but generation stays on pi4.

Search is on by default in the composer. You can still turn it off; the router also triggers search when your message clearly asks for news or the web. The public site uses Tailscale Funnel on pi4 to port 18080. From 11:00 PM to 7:00 AM Pacific time the port shows a downtime banner instead of chat, and in memory chats are cleared when the service stops for the night.

## Try it

Chat: https://rpi-pi4.taild6c87d.ts.net/

API reference (same host): https://rpi-pi4.taild6c87d.ts.net/docs

## For developers

Setup, install on a board, deploy, datasets, and operator scripts are documented under `pi-pair/docs`. Start with [Architecture](pi-pair/docs/ARCHITECTURE.md) and [API](pi-pair/docs/API.md). [Operations](pi-pair/docs/OPERATIONS.md) covers day to day running. [Deploy](pi-pair/docs/DEPLOY.md) covers CI and syncing to pi3.

In this repo, `make check` runs linting, typecheck, shellcheck, Python tests, page tests, and checks that `pi-pair/static` matches a fresh build. `bash pi-pair/install.sh` on a board copies the `pair/` package and prints the `systemctl --user` lines to enable.