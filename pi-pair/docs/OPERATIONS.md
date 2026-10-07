# Operations

## Start

From a checkout, or from `~/pi-pair` after install:

```bash
cd pi-pair
python3 mini_chat.py
```

`bash start.sh` is the same command. Default bind is `0.0.0.0:18080`. The process serves the files already in `static/`. Rebuilding the page is a development step and is not part of the running server:

```bash
cd pi-pair/web
npm ci
npm run build
```

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

Repeat the rsync for pi2 and pi4. The unit's role comes from the hostname: pi2 is health, pi3 is dataset, pi4 is brain. Only the brain unit pulls Flash, `qwen3:0.6b`. It does not pull Pro. On pi4 the same script removes `snowflake-arctic-embed:m` when that tag is still installed, and removes every other `snowflake-arctic-embed` tag. pi2 prints `Skipping model pull` and does not install a chat model. pi3 does not install a chat model. Details for the pi3 embed tag are in `pi-pair/docs/EMBED.md`. On pi4, `ollama-lan.service` should set `OLLAMA_MAX_LOADED_MODELS` to at least 2, `OLLAMA_KEEP_ALIVE=-1`, and `OLLAMA_NUM_PARALLEL` from `inference_pi4.json` (default 2); `install.sh` writes that user unit with `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_KEEP_ALIVE=-1`, and that parallel value. Pro (`qwen3:1.7b`) is not pulled by the installer. If it is missing, on pi4 only: `ollama pull qwen3:1.7b`. A request never runs that pull. An existing `canned_map.json` on the Pi is left in place so a folded map is not replaced by the seed.

Attachment OCR uses apt packages, not pip. On each Pi that serves the page:

```bash
sudo apt-get install -y tesseract-ocr poppler-utils
```

`tesseract` reads an image. `pdftoppm` from `poppler-utils` turns a JPEG-scanned PDF into JPEG pages before tesseract. `install.sh` prints that command when either binary is missing. The unit tests mock OCR, so CI does not install them.

| Name | Host | Port | Role |
| --- | --- | --- | --- |
| pi2 | 10.0.0.180 | 18080 | Health and read-only canned mirror. No chat model. |
| pi3 | 10.0.0.228 | 18080 | Dataset, queue, train-then-delete. No chat model. |
| pi4 | 10.0.0.166 | 11434 | Ollama. Sole generative brain. The page on this board is still port 18080. |

A pinned peer that is down returns `<name> offline`. A pinned weak peer returns the brain refusal above, whether or not the process is up. Auto does not move a miss onto another Pi.

## Failures

| Situation | What the caller sees |
| --- | --- |
| Map hit | Chip `cache`. No call to pi4's chat model. |
| Line that is not a normalized map key | Treated as a map miss, then the pi4 chat rule. |
| Map miss, pi4 up | Chip `brain: pi4`. Queue row on pi3. |
| Map miss, pi4 down | `pi4 unreachable on cache miss. Refusing to answer from pi2 or pi3.` |
| Cap full | The chat waits for a free slot (queue of 8, about 60 seconds). The page shows `Waiting for a free slot…`. A full queue or a timed-out wait is HTTP 503 and one short line, with no board name. An exact map hit or a page answer does not take a slot. `POST /api/chat` uses that same queue. |
| Pin or direct to pi2 or pi3 | `cannot be the brain` sentence. No model call, including when the map would have hit. |
| Map file unreadable | Treated as a miss, then the pi4 rule. |
| Train config names an unknown dataset | Job exits before the queue is moved. |
| Down vote, no correction | The row is queued, then dropped at fold time. |
| Correction on a known line | That sentence replaces the stored map entry. |
| Held-out text already in the map | Gate fails, queue restored, nothing promoted. |
| Train job on pi4 or pi2 | Refused. The dataset role is pi3. |
| Attachment over 4 MB | `attachment is over 4 MB` |
| Attachment is not txt, md, an image, or a JPEG-scanned PDF | `unsupported file type`, or `only a JPEG-scanned PDF can be read` |
| OCR binaries missing on the Pi | `OCR is not installed on this Pi` |
| OCR still running after 20 seconds | The process group is killed. The upload is `could not read that file`. |
| A third OCR while two are running | `OCR is busy` |
| `/api/chat` or `/api/health` with no key or the wrong key | `missing or invalid API key` and status 401. |
| `PI_GPT_API_KEY` unset | Those two routes return 503. The page and `/health` still answer. |

