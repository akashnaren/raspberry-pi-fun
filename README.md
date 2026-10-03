# pi-pair

Chat router for the pi2, pi3, and pi4 fleet. Stdlib Python only (no pip). It listens on **18080**. A known line is answered from the canned map. Anything the map does not contain is generated on pi4 and only on pi4.

The local model is small. Search and the canned map are how it answers facts it does not know.

This tree is the software half of that loop. [PR #23](https://github.com/akashnaren/raspberry-pi-fun/pull/23) merged on 2026-10-02 (`45050ee`). After that deploy of main, live end-to-end stages 1–5 on the boards were GREEN. Product Ship was YES the same day, about 13:40 PT. The prove packet for that run is the post-#23 board record at `45050ee` (fleet handoff; not a file in this tree). Hugging Face holds datasets only so far; this document does not claim published model weights.

## Hardware

pi2, pi3, and pi4 all sit in one custom 3D-printed server rack. Tailscale names are rpi-pi2, rpi-pi3, and rpi-pi4.

![3D-printed vertical rack with three Raspberry Pis](pi-pair/docs/rack/rack-hero.jpg)

![Front ports, USB Wi-Fi adapters, and antennas](pi-pair/docs/rack/rack-front.jpg)

![Top view of the rack enclosure](pi-pair/docs/rack/rack-top.jpg)

## What local inference means here

A reply is local when the tokens, or the stored sentence that stands in for them, are produced on this rack. The phone opens a page on a Pi. The router on that Pi decides. Either it reads a sentence that was already written down, or it asks pi4 to run one forward pass of a small language model and stream the tokens back. The phone does not call a hosted chat API for this path, and a miss is not an excuse to borrow a model from a weaker board.

The mesh exists because the three boards are not interchangeable computers that happen to share a switch. One of them can decode. One of them can hold the growing map and the short-lived training files. One of them can stay up, answer a health probe, and keep a read-only copy of the map. The router is the piece that makes that split visible on every turn: Auto, a pin, or a direct call, each with a different rule, each reported on the answer as a chip.

Working memory and the dataset are different things, and mixing them up is how a small fleet fills its SD cards and then lies about what it knows. Working memory is the weights, the activations, and the key/value cache for the turn that is being decoded right now. It lives in RAM on pi4 and disappears when that turn ends, apart from whatever `keep_alive` has left resident. The dataset is the canned map: a compact `input → answer` table. It is durable, it is small, and it is allowed on pi3 and, as a mirror, on pi2. A cache hit never enters the transformer. A cache miss never becomes an excuse to treat pi2 or pi3 RAM as a second brain.

## Why the shape is pi4-only, then a map, then train and delete

The fleet inventory for this rack, recorded in 2026-10-02, is the reason the router is not a round-robin.

pi2 is about 1GB of RAM and an armv7 userland. Ollama does not support that board. Its job is health probes and, if wanted, a read-only mirror of the canned map. It does not generate.

pi3 is about 1GB of RAM and arm64. A 0.5B model can be loaded there. Generation is not reliable: short prompts come back as non-answers. pi3 keeps the dataset, the bounded miss queue, the prepared files, and the train-then-delete job. It does not emit chat tokens for a user.

pi4 is about 8GB. It is the only board in this fleet that has actually produced usable generative replies. New lines go there. Pins that name pi2 or pi3 as the brain are rejected in the router and again in the chat client, even if a config file has been hand-edited to say otherwise. The failure is named. The router does not quietly ask pi3 to try.

Speed is the other half. Most repeated lines in a household or an office are not new. The canned map answers those without a decode. The first time a line is new, pi4 pays for it once. The exchange is queued on pi3. A later job may fold that pair into the map, then the raw queue file and the prepared shard are deleted. The next time the same line shows up, Auto is a map read again. Disk pressure is why the delete step is mandatory. A 1GB board that keeps every transcript will fill the card; a full card takes the map down with it. The public synthetic seeds are the starting map. Live household text stays on pi3 until the job deletes it, and it is not what gets published.

Train-then-delete is our rule. The open-source trainers cited below compact data before a run and keep stage checkpoints. They do not, as shipped, wipe the raw corpus when the run finishes. We do, because these cards are small.

## Boards, models, and what each one may hold

| Board | Inventory | May hold | Must not hold |
| --- | --- | --- | --- |
| pi2 | ~1GB, armv7, Ollama unsupported | Health probes; read-only copy of `canned_map.json` | Chat weights, a train queue, generated tokens |
| pi3 | ~1GB, arm64; 0.5B can load and still gibberish | Canned map, seed files, bounded queue, prepared shards, adapter manifests, the train job | User-facing generation |
| pi4 | ~8GB, the only proven generative board | Quantized chat weights in Ollama; the decode for a miss; an active adapter manifest after the gate | Raw train shards, the train corpus, a second full fine-tune beside live decode |

The model on pi4 starts as `qwen2.5:0.5b`. The published config for Qwen2.5-0.5B-Instruct (`model_type` qwen2) is 24 layers, hidden size 896, intermediate size 4864, 14 query heads and 2 key/value heads, vocabulary 151936, rope theta 1,000,000, SiLU, tied embeddings, and a card maximum of 32768 positions. Source: the model config published at `https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct`. This fleet does not use that full context. `configs/runtime/inference_pi4.json` caps `num_ctx` at 2048 so the key/value cache stays inside the 8GB board with the weights and the operating system. `OLLAMA_NUM_PARALLEL` is 1: one sequence at a time, in the Ollama service and in spirit in the router slot cap. `keep_alive` in that same file is `5m`, an operator choice so a short run of misses does not reload the weights every turn. Temperature for ordinary chat sits between 0.6 and 0.8; the page defaults to 0.7, inside that band. A move up to `llama3.2:1b` waits on a health check that the process stays resident. This document does not invent a layer count for that larger model; the card is gated and was not read here.

pi2 and pi3 do not get a pull of those weights. `install.sh` skips `ollama pull` unless the hostname is the pi4 role. A pin of pi2 or pi3 returns:

`pi2 cannot be the brain. Generative inference runs only on pi4. This peer does not run a chat model.`

The same sentence is used for pi3, with the name changed. If Auto misses and pi4 does not answer, the router returns:

`pi4 unreachable on cache miss. Refusing to answer from pi2 or pi3.`

A corrupt or unreadable map is treated as a miss and follows that same rule. It is not repaired by a weak model.

## Office roles

pi4 is the only generative employee. pi3 is the dataset employee: it stores the map, accepts the bounded queue, and runs the job that adapts the map and then deletes the shards. pi2 is health and mirror only. The chat page can run on any of the three addresses below. Generation does not follow the page. It follows the rules above.

## Flow from the person to the answer

The person is on the phone, on the page served at port 18080. The router is whichever Pi answered that HTTP request.

If the mode is Auto, the router normalizes the latest user turn (lowercase, collapsed whitespace, trailing punctuation removed) and looks it up in `data/canned/canned_map.json`. A hit returns that sentence with the chip `cache`. pi4 is not called. A miss is sent only to pi4. The chip on that answer is `brain: pi4`. The prompt and the answer are appended to the bounded queue on pi3 (at most 128 rows; older rows fall off the front). If this router is itself running as the dataset role, it writes the file locally. If it is running as the brain or as health, it forwards the row to pi3 and does not keep a copy.

On a miss, before pi4 is called, the router may look the line up on DuckDuckGo. It keeps a few result titles, links, and short snippets. It may read one of those pages as plain text, with a size cap and a short timeout, and it does not follow links from that page. That text is added only to the prompt pi4 sees. The queue still stores the person's line and the model's answer. If the lookup fails, pi4 still answers from the local model and the page says search failed. This is not a hosted chat API and it is not a second generator.

If the mode is a pin of pi4, the same map may still answer, and a miss still goes to pi4. If the mode is a pin of pi2 or pi3, the router refuses before any map read and before any HTTP call to a model. Mesh off is the direct path: the canned map is skipped and the named peer is called, but only if that peer is allowed to generate. Direct to pi2 or pi3 is the same refusal. Direct to pi4 is pi4's Ollama, through this router, with the chip `brain: pi4`.

```mermaid
flowchart TD
  U[Person on the chat page] --> R[Router on whichever Pi served port 18080]
  R --> M{Mode}
  M -->|Auto or pin pi4| Q{Key in canned_map.json?}
  Q -->|yes| C[Return the stored sentence]
  C --> ChipC[Chip cache]
  Q -->|no| B[pi4 decode only]
  B --> ChipB[Chip brain: pi4]
  B --> L[Bounded queue on pi3]
  M -->|Pin pi2 or pi3| X[Named refusal]
  M -->|Mesh off| D{Peer allowed to generate?}
  D -->|pi4| B2[pi4 decode, map skipped]
  D -->|pi2 or pi3| X
  B -->|pi4 down| E[Named miss failure]
```

## The closed cycle

The loop is meant to be run, not only drawn.

1. **Pre-train seed.** The synthetic canned map, the SFT and preference seeds, and the held-out eval file are already in `data/`. They are the same rows published as the three dataset repos named below. No live transcript is in them.
2. **Weights on pi4.** `install.sh` on the pi4 role pulls `qwen2.5:0.5b` and sets `OLLAMA_NUM_PARALLEL=1`. The other two roles skip the pull.
3. **Chat on port 18080.** People and agents use the page. Auto hits do not decode. Misses decode on pi4.
4. **Collect.** Each successful generative completion is one row in `data/train/pending/queue.jsonl` on pi3. Hits increment a counter in `data/metrics.json` and are not stored as text. There is no unbounded chat archive. A thumbs vote, and a corrected sentence when someone writes one, is written onto that same row.
5. **Analyze.** The held-out file is the gate, not a sample of the queue. A queued line whose normalized text is a held-out input is dropped, not folded.
6. **Post-train.** On pi3, `python3 scripts/lifecycle/post_train.py` checks `dataset_info.json` first. Every name in `configs/train/sft_canned.yaml` must be registered and the file must exist, or the job stops before it touches the queue. It then moves the queue into `data/train/active/`, writes a normalized copy under `data/prepared/`, and folds accepted new pairs into a candidate map. Existing keys are left alone unless the row has a `correction`, which replaces the stored sentence. A `vote` of `down` with no correction is not folded. An up vote, or a row with no vote, still folds only new keys. This step does not run a gradient update and does not pretend to. A full LoRA or SFT trainer is the shape of the YAML and the registry; the job that actually runs on this 1GB board updates the canned map, which is the artifact the router serves. The adapter manifest records that the weights remain the Ollama model on pi4.
7. **Gate.** Held-out inputs must be absent from the candidate. Keys that were already in the map must still be there. A failed gate puts the queue back and does not promote.
8. **Promote.** The manifest moves to `adapters/active/manifest.json`. The candidate replaces `data/canned/canned_map.json`. Nothing in `adapters/` is a weight file.
9. **Delete.** The active shard and the prepared file are removed. `data/train/done/<id>.json` is a tombstone: counts and a hash of the map, no prompt and no answer. `scripts/lifecycle/delete_shards.py` can sweep leftovers in `data/train/active` and `data/prepared`. It does not delete `data/train/pending`, so a new queue is safe. It does not delete `data/canned/canned_map.json`.
10. **Repeat.** The next Auto turn can hit the line that was just folded.

Deleting the queue does not make the model larger or smaller. The served weights stay the Ollama model on pi4. This job folds accepted pairs into the canned map. A full fine-tune is not what this 1GB board runs.

You can tell the cycle worked. The queue row on pi3 had the prompt, the answer, and the vote. `post_train` added a key, or the tombstone recorded the label count. The active shard and the prepared file are gone. A tombstone is under `data/train/done`. Asking that line again is a map hit.

When the card is at least 85% full, or the pending queue files pass 1MB, the oldest raw queue rows are deleted. That frees the label text. It does not delete the canned map and it does not change the weights on pi4.

pi2 may receive a copy of `canned_map.json` and must not run the job. The role check refuses `post_train` unless the process is the dataset role. pi4 must not receive the queue files or the prepared shards. Forwarding is how a page served on pi4 still feeds pi3 without leaving the text on the brain.

```mermaid
flowchart LR
  Seed[data/canned and data/seed] --> Map[canned_map.json]
  Map --> Hit[Auto hit]
  Miss[pi4 miss] --> Pending[data/train/pending]
  Pending --> Active[data/train/active]
  Active --> Prep[data/prepared]
  Prep --> Fold[Fold new pairs]
  Fold --> Gate[eval_heldout gate]
  Gate -->|pass| ActiveAd[adapters/active manifest]
  ActiveAd --> Map
  Gate -->|pass| Done[tombstone in data/train/done]
  Done --> Del[Delete active and prepared]
  Gate -->|fail| Pending
```

Weekday improvement goes through this page, not through a side channel. The standing routine named `pi-flywheel-improve` is owned by the CTO. The operator path is the one above: open the page, work in Auto, and on pi3 run `post_train` when the queue should be folded. This document does not schedule that routine.

## Neural net: what runs, and only on a miss

A cache hit does not tokenize, embed, attend, or sample. The router compares a normalized string to keys in a JSON object and returns the stored string. That is the whole hot path. Calling it a network would be a description of a different system.

A miss on pi4 runs one decoder-only transformer, the Qwen2 stack behind `qwen2.5:0.5b`, inside Ollama. The request asks for `num_ctx` 2048, `keep_alive` 5m, and the caller's temperature. One sequence is in flight. The layers below are the published block, walked in the order a token is produced. They are not a custom net written in this repo.

**Tokens.** The user text is byte-pair encoded into integer ids. The id sequence is what the stack sees. The 151936-way vocabulary is why the last projection is expensive relative to the width of the model, and why a tied embedding (the same matrix for ids-in and logits-out) is how this checkpoint spends that parameter budget.

**Embeddings.** Each id is a row of the token embedding, width 896. Positions are not a second learned table added on the side. Qwen2 uses rotary position on the query and key vectors inside attention, with the published rope theta of 1,000,000. The context cap of 2048 is applied by the server, below the card maximum, so the cache of past keys and values cannot grow to the card's 32768.

**A block, 24 of them.** Each layer is the same shape. RMSNorm, then grouped-query attention, then a residual add. RMSNorm again, then a SwiGLU feed-forward (SiLU on the gate projection, intermediate width 4864), then another residual add. Grouped-query attention is the part that makes an 8GB decode practical: 14 query heads share 2 key/value heads, so the cache stores two key/value streams rather than fourteen. Inside the attention of one new token the model forms queries, keys, and values, rotates queries and keys, takes the softmax over the past positions up to the cap, mixes the values, and projects back to width 896. The feed-forward is a position-wise expansion and contraction. It does not look at other tokens. Attention is where the token sees the rest of the line.

**Logits and the next id.** After the 24th block, a final RMSNorm and the tied output projection produce one logit per vocabulary row. Temperature scales those logits. A sample (or a greedy pick, if temperature is zero) chooses the next id. That id is appended, the key/value cache already holds the previous positions, and the next step does not recompute them. The new id is detokenized into text and streamed back through this router as server-sent events. The chip on that stream is `brain: pi4`.

**What this is not.** It is not a convolutional network. A convolution ties one small kernel across a grid and is the right bias when the signal is a neighborhood: pixels, a spectrogram, a local patch of a sensor. Next-token chat is a long chain of discrete ids whose relevant context can be anywhere in the 2048-token window, not in a fixed local patch. The attention block is there specifically because a convolution would have to be stacked very deep, or dilated until it stopped looking like a local kernel, to see that far. This fleet does not run a vision encoder, and the page does not decode images. CNNs remain the usual tool if a later sensor or camera path is added beside the chat. They are not on the path that turns a missed sentence into tokens, and they are not a substitute for pi4 when pi4 is down.

Recurrent nets and state-space models are also not the runtime here. The deployed checkpoint is the transformer above. Swapping the block type would be a different model, a different pull, and another health check. It is not a fallback for a weak board.

The key/value cache is working memory. It is not copied into the canned map. The map stores finished strings. After the turn, the queue stores the prompt and the finished answer, on pi3, until the job deletes them. Those three stores stay separate so a full disk, a restart, or a training run cannot be mistaken for the model still "remembering" the conversation in RAM.

## Dataset layers

The data-stack note of 2026-10-02 is the layer split this tree follows. Operators are not supposed to call all of it "memory."

| Layer | Durable | Where | In this repo |
| --- | --- | --- | --- |
| Working memory | No | pi4 RAM only | Not stored |
| Canned map | Yes, compact | pi3 read/write; pi2 read-only mirror; pi4 may read | `data/canned/canned_map.json` and `canned_seed.jsonl` |
| Train queue | No, bounded | pi3 only | `data/train/pending/queue.jsonl` then `active/` |
| Prepared artifacts | No, deleted with the shard | pi3 | `data/prepared/` |
| Adapter manifest | Yes, after the gate | Staged on pi3; the active file names pi4's weights | `adapters/staging/`, `adapters/active/manifest.json` |
| Chat weights | Yes, budgeted | pi4 Ollama only | Not in git |
| Eval held-out | Yes | Authored with the seeds; the gate runs on pi3 | `data/seed/eval_heldout/eval_heldout.jsonl` |
| SFT and preference seeds | Yes | pi3 consumes them; they are not the live queue | `data/seed/sft_seed/` |

`data/dataset_info.json` is the registry, in the same spirit as LLaMA-Factory's `dataset_info.json`. A train file may name `pi_flywheel_canned`, `pi_flywheel_sft_chat`, `pi_flywheel_sft_alpaca`, `pi_flywheel_preference`, and `pi_flywheel_eval_heldout`. It may not name a path that was never registered. The same registry is repeated under `data/seed/dataset_info.json` and `data/seed/schemas/dataset_info.json` with paths adjusted for those directories, so a sync of `data/seed/` still resolves. The loader prefers `data/dataset_info.json`.

Counts in the vendored synthetic seed: 111 canned rows, 20 chat SFT rows, 6 alpaca rows, 8 preference pairs, 15 held-out eval rows. Held-out inputs are not keys of the canned map. The rows are synthetic. They are not household logs.

Public copies, which are the canonical synthetic seeds and not a dump of the live queue:

- https://huggingface.co/datasets/akashnaren/pi-flywheel-canned
- https://huggingface.co/datasets/akashnaren/pi-flywheel-sft-seed
- https://huggingface.co/datasets/akashnaren/pi-flywheel-eval

No other hub URL is claimed here. Live queue text is not part of those repos.

## How the open-source trainers shaped the directories

The layout is a small reading of eight codebases, applied to a three-board rack rather than copied as a cluster stack. Train-then-delete is ours; the directory habits are theirs.

| Source | What we kept |
| --- | --- |
| [Axolotl](https://github.com/axolotl-ai-cloud/axolotl) | One config file owns a run. `configs/train/sft_canned.yaml` names the datasets and the eval set. The job refuses to start if a name is not in the registry. |
| [AllenAI Open-Instruct](https://github.com/allenai/open-instruct) | Stages are artifacts. Eval sits beside train and is not mixed into the queue. A gate runs before promote. |
| [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) | `dataset_info.json` is the schema registry. Columns and `stage` are explicit. Unregistered JSON is not a dataset. |
| [Hugging Face TRL](https://github.com/huggingface/trl) | One command per method. Here the command is `python3 scripts/lifecycle/post_train.py`. Preference columns are registered for a later trainer; this job does not run DPO. |
| [LitGPT](https://github.com/Lightning-AI/litgpt) | Verbs stay separate from the chat server. The train entry is a script. The router does not contain the fold. |
| [nanoGPT](https://github.com/karpathy/nanoGPT) | Prepare is a step that emits an artifact. Training reads that artifact. We delete it afterwards; nanoGPT keeps `train.bin`, and we cannot afford to. |
| [TinyLlama](https://github.com/jzhang38/TinyLlama) | Pretrain text and chat SFT are different trees (`data/seed/sft_seed` versus the canned map). The edge runtime is a quantized model on the strong board, not a second copy of the corpus. |
| [Unsloth](https://github.com/unslothai/unsloth) | Adapters, not a full fine-tune, are the intended weight update on a small machine. This repo stores the manifest and leaves the GGUF or Ollama weights on pi4. It does not merge a LoRA into a quantized base; that warning from LLaMA-Factory's export notes is why the manifest points at the base model instead of shipping a merged file we did not build. |

DeepSpeed, FSDP, and multi-node launchers from those repos are not imported. They are for a cluster this rack is not. `pi-pair/docs/SOURCES.md` repeats the URLs.

## The page

The daily tool is the page on port 18080, on the LAN addresses in the fleet map. Tailscale names work the same way when the tailnet is up. The page is one column: messages, a composer fixed at the bottom, and a Low / Medium / High control beside that composer. The control defaults to Medium. qwen2.5:0.5b has no separate reasoning channel, so the router sends the level as Ollama `num_predict` and temperature: Low is 64 tokens at 0.6, Medium is 256 at 0.7, High is 768 at 0.8. Those are different decode requests. The reply is marked with the level that was used. All three still go through Auto. The page does not say which board answered. That stays on the response headers.

The composer keeps the whole session. Each new line is sent with the earlier turns, and a follow-up is not answered from the canned map. Enter sends the line. Shift+Enter, or Ctrl+Enter, inserts a newline. Stop ends the reply that is still arriving. Regenerate asks the same line again. Edit changes an earlier line and sends from there, dropping the turns after it. Copy is on each message.

When the map misses, the reply shows a short searched note and the source links. If the lookup fails, the note says search failed and the answer is still from the local model.

Under a finished answer there is a thumbs up, a thumbs down, and Correct. Correct is an optional replacement sentence. Those controls call `POST /v1/flywheel/feedback`.

| Who | Page | Model port |
| --- | --- | --- |
| pi4 | http://10.0.0.166:18080/ and http://rpi-pi4:18080/ | http://10.0.0.166:11434/ Ollama, not the page |
| pi3 | http://10.0.0.228:18080/ and http://rpi-pi3:18080/ | none for chat |
| pi2 | http://10.0.0.180:18080/ and http://rpi-pi2:18080/ | none for chat |

There is no separate control plane. A pin of pi2 or pi3 is still refused by the router; the page itself does not offer that pin.

## Layout

```
pi-pair/
  mini_chat.py              start here
  start.sh                  same command
  pair/                     router, canned lookup, queue, train cycle
  static/                   chat page
  peers.example.json        fleet map
  data/dataset_info.json    registry
  data/canned/              canned_map.json, canned_seed.jsonl
  data/seed/                sft_seed, eval_heldout, schemas
  data/train/               pending, active, done
  data/prepared/            ephemeral, deleted after the job
  adapters/                 staging and active manifests, not weights
  configs/train/            sft_canned.yaml
  configs/runtime/          pi4 knobs and the map pointer
  scripts/                  prepare_queue, run_sft, run_gate, post_train, delete_shards
  install.sh                user systemd unit
  mesh-hello.sh             health, peer probe, one chat
  test_pair.py              router tests
  test_flywheel.py          registry, queue, train-then-delete
  docs/rack/                photos of the 3D-printed rack
  CI-CD.md                  development vs production, secrets, re-run
  ci/                       data-stack validator and the pi3 deploy script
```

This file is the repo README. There is not a second one inside `pi-pair/`.

`peers.json` is gitignored. `install.sh` creates it from the example only when the Pi does not already have one. The queue, prepared shards, metrics, and adapter manifests are gitignored. The canned map in git is the synthetic seed; a Pi that has already folded new lines keeps its map across reinstall.

## Start

From a checkout, or from `~/pi-pair` after install:

```bash
cd pi-pair
python3 mini_chat.py
```

`bash start.sh` is the same command. Default bind is `0.0.0.0:18080`.

Smoke without a model process (peers show down; that is fine):

```bash
python3 mini_chat.py
curl -sS http://127.0.0.1:18080/health
```

Tests:

```bash
python3 -m unittest discover -s pi-pair -p 'test_*.py'
```

With the fleet up, from a Pi that is running the router:

```bash
bash mesh-hello.sh
```

On pi3, after some misses have queued:

```bash
python3 scripts/data/prepare_queue.py
python3 scripts/eval/run_gate.py
python3 scripts/lifecycle/post_train.py
```

`scripts/train/run_sft.py` is the same cycle as `post_train.py`. The fold is not offered as a step that leaves shards behind.

## Install on a Pi

```bash
rsync -av pi-pair/ pi3:~/pi-pair/
ssh pi3
cd ~/pi-pair
bash install.sh
systemctl --user daemon-reload
systemctl --user enable --now pi-pair.service
```

Repeat the rsync for pi2 and pi4. The unit's role comes from the hostname: pi2 is health, pi3 is dataset, pi4 is brain. Only the brain unit is told to pull `qwen2.5:0.5b`. The other two print `Skipping model pull` and do not install a chat model. An existing `canned_map.json` on the Pi is left in place so a folded map is not replaced by the seed.

| Name | Host | Port | Role |
| --- | --- | --- | --- |
| pi2 | 10.0.0.180 | 18080 | Health and read-only canned mirror. No chat model. |
| pi3 | 10.0.0.228 | 18080 | Dataset, queue, train-then-delete. No chat model. |
| pi4 | 10.0.0.166 | 11434 | Ollama. Sole generative brain. The page on this board is still port 18080. |

A pinned peer that is down returns `<name> offline`. A pinned weak peer returns the brain refusal above, whether or not the process is up. Auto does not move a miss onto another Pi.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/` | Chat page |
| GET | `/health`, `/peers` | Router plus peer health. `generative` is false on pi2 and pi3. |
| POST | `/v1/chat/completions` | OpenAI chat. `stream:true` is SSE. Same call for a person and for another agent. |
| POST | `/v1/flywheel/enqueue` | Miss row. Accepted only on the dataset role. |
| POST | `/v1/flywheel/feedback` | Label a completion. `vote` is `up` or `down`. `correction` is optional. Omit `prompt` and `answer` to rate the last completion this router returned. |

Target a peer with these headers:

```http
POST /v1/chat/completions
X-Pi-Target: auto | pi2 | pi3 | pi4
X-Pi-Mesh: on | off
```

JSON fields `pi_target` and `pi_mesh` are accepted and stripped before a worker would see the body. Mesh on, Auto or pin pi4: map, then pi4. Mesh off: skip the map, still refuse pi2 and pi3. The response headers `X-Pi-Chip` and `X-Pi-Peer` are `cache` or `brain: pi4` / `pi4`. The page does not print those headers.

## Agents

Other agents on this fleet use the same router. There is no second bot, no schedule, and no paid model. POST a chat turn to any board's port 18080. A miss is still generated only on pi4, and the row is still queued on pi3.

```bash
curl -sS http://127.0.0.1:18080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"status"}],"stream":false}'
```

`X-Pi-Target: auto` and `X-Pi-Mesh: on` match the page. A pin of pi2 or pi3 is refused.

Rate the last completion that router returned:

```bash
curl -sS http://127.0.0.1:18080/v1/flywheel/feedback \
  -H 'content-type: application/json' \
  -d '{"vote":"down","correction":"the sentence you wanted"}'
```

`vote` is `up` or `down`. `correction` is optional. Send `prompt` and `answer` when the turn you mean might not be the last one. The dataset host writes `prompt`, `answer`, `vote`, and `correction` (when present) onto `data/train/pending/queue.jsonl`. `post_train` on pi3 reads that file. A down vote with no correction is dropped. A correction is the sentence that gets folded.

## Environment

| Var | Default | Meaning |
| --- | --- | --- |
| `PI_PAIR_HOST` | `0.0.0.0` | Bind address |
| `PI_PAIR_PORT` | `18080` | Page and router |
| `PI_PAIR_PEERS` | `./peers.json` if it exists, else the built-in fleet | Peer list |
| `PI_PAIR_ROLE` | from `PI_PAIR_NAME`, else dataset | `brain`, `dataset`, or `health` |
| `PI_PAIR_NAME` | unset | Hostname hint (`pi2`, `pi3`, `pi4`) |
| `PI_PAIR_DATA` | `data/` | Root of the map, seeds, and queue |
| `PI_PAIR_CANNED` | `data/canned/canned_map.json` | Override the map file |
| `PI_PAIR_ADAPTERS` | `adapters/` | Manifest directory |
| `PI_PAIR_TRAIN_CONFIG` | `configs/train/sft_canned.yaml` | Run file |
| `MESH_MODEL` | `qwen2.5:0.5b` | Model name when the request omits one |
| `PI_PAIR_SLOTS` | `3` | Concurrent router cap. Ollama itself stays at one parallel sequence. |
| `PI_PAIR_HEALTH_TTL` | `2.5` | Seconds to cache peer probes |

## Failures

| Situation | What the caller sees |
| --- | --- |
| Map hit | Chip `cache`. No call to pi4. |
| Map miss, pi4 up | Chip `brain: pi4`. Queue row on pi3. |
| Map miss, pi4 down | `pi4 unreachable on cache miss. Refusing to answer from pi2 or pi3.` |
| Pin or direct to pi2 or pi3 | `cannot be the brain` sentence. No model call, including when the map would have hit. |
| Map file unreadable | Treated as a miss, then the pi4 rule. |
| Train config names an unknown dataset | Job exits before the queue is moved. |
| Down vote, no correction | The row is queued, then dropped at fold time. |
| Correction on a known line | That sentence replaces the stored map entry. |
| Held-out text already in the map | Gate fails, queue restored, nothing promoted. |
| Train job on pi4 or pi2 | Refused. The dataset role is pi3. |

## CI/CD

Pull requests into `main` use the GitHub Environment `development` (lint, unit tests, data-stack validation). A push to `main` uses the GitHub Environment `production`, joins Tailscale with `TS_AUTHKEY`, and syncs this tree plus `data/canned` to pi3. Names of the secrets, the dry-run input, and how to add a check are in [pi-pair/CI-CD.md](pi-pair/CI-CD.md).
