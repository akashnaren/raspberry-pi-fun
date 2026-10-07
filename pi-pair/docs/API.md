# API

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/` | Chat page |
| GET | `/health`, `/peers` | Router plus peer health. `generative` is false on pi2 and pi3. No API key. |
| POST | `/v1/chat/completions` | OpenAI chat. `stream:true` is SSE. Same call for a person and for another agent. No API key. |
| POST | `/v1/attachments` | One file, multipart or a raw body with `X-Filename`. `.txt` and `.md` are decoded. Images and JPEG-scanned PDFs are OCR'd locally, then the same text is returned for the chat. Body cap 4 MB. Text cap 4096 characters. First 5 PDF pages. |
| POST | `/v1/flywheel/enqueue` | Miss row. Accepted only on the dataset role. |
| POST | `/v1/flywheel/feedback` | Label a completion. `vote` is `up` or `down`. `correction` is optional. Omit `prompt` and `answer` to rate the last completion this router returned. |
| GET | `/openapi.json`, `/swagger.json` | OpenAPI document for the keyed API. No API key. |
| GET | `/docs`, `/swagger` | Swagger UI for that document. No API key. |
| GET | `/api/health` | Same peer snapshot as `/health`, plus `public_model`. Requires the API key. |
| POST | `/api/chat` | One chat turn for another app. Requires the API key. Omitted `mode` selects Flash. |

Target a peer with these headers:

```http
POST /v1/chat/completions
X-Pi-Target: auto | pi2 | pi3 | pi4
X-Pi-Mesh: on | off
```

JSON fields `pi_target` and `pi_mesh` are accepted and stripped before a worker would see the body. Mesh on, Auto or pin pi4: map, then pi4. The map is first, including ahead of any model load. Mesh off: skip the map, still refuse pi2 and pi3. The response headers `X-Pi-Chip` and `X-Pi-Peer` are `cache` or `brain: pi4` / `pi4`. The page does not print those headers.

LAN mode is `flash`, `pro`, or `auto`, sent as JSON `mode` or `pi_mode`, or header `X-Pi-Mode`, on `/v1/chat/completions`. Omitted, blank, or anything else is Flash (`qwen3:0.6b`). A model string that is not the Flash tag or the Pro tag is clamped to those two (`model` and `pro_model` in the runtime config). `pro`, or a model string of `qwen3:1.7b` when mode is omitted, selects Pro. Explicit `flash` wins over a Pro model string. `auto` reads the line and answers on Flash or Pro; the response header `X-Pi-Route` and the field `pi_route` name that choice so the page can show it. The response header `X-Pi-Mode` and the field `pi_mode` repeat the requested mode. Low, Medium, and High still arrive as `think`. Low and Medium are direct answers. High sets Ollama `think` true, caps the reasoning channel at 192 tokens or 25 seconds, then requests the answer separately. Auto keeps that High turn on Flash unless the request chose Pro. Flash and Pro share the inference-slot gate. A map hit does not call Ollama and does not take a slot. Pro does not unload Flash. If Pro has not been pulled, the router returns the pull line and does not start a fetch. A Pro stream, including Auto that chose Pro, sends Thinking before the model request. Search results on that reply are capped at eight links. The keyed `POST /api/chat` `mode` is not this switch.

## Agents

Other agents on this fleet use the same router. A bot is another caller of this HTTP API. There is no second page, no schedule, and no paid model. POST a chat turn to any board's port 18080. An app that is not the page on that port uses `POST /api/chat` and `PI_GPT_API_KEY`, described under Public API. A miss is still generated only on pi4, and the row is still queued on pi3.

`pi-pair/scripts/qa/chat_label.py` does what the page does for one line. It POSTs the turn with Auto and mesh on, then POSTs a thumbs vote, and a correction when you pass one, so the row on pi3 has the label `post_train` reads. It does not pin pi2 or pi3.

```bash
python3 pi-pair/scripts/qa/chat_label.py --base http://127.0.0.1:18080 --prompt 'status' --vote up
python3 pi-pair/scripts/qa/chat_label.py --dry-run --prompt 'status' --vote down --correction 'the sentence you wanted'
```

`--dry-run` prints those two posts and does not open a connection.

```bash
curl -sS http://127.0.0.1:18080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"qwen3:0.6b","messages":[{"role":"user","content":"status"}],"stream":false}'
```

`X-Pi-Target: auto` and `X-Pi-Mesh: on` match the page. A pin of pi2 or pi3 is refused.

Rate the last completion that router returned:

```bash
curl -sS http://127.0.0.1:18080/v1/flywheel/feedback \
  -H 'content-type: application/json' \
  -d '{"vote":"down","correction":"the sentence you wanted"}'
```

`vote` is `up` or `down`. `correction` is optional. Send `prompt` and `answer` when the turn you mean might not be the last one. The dataset host writes `prompt`, `answer`, `vote`, and `correction` (when present) onto `data/train/pending/queue.jsonl`. `post_train` on pi3 reads that file. A down vote with no correction is dropped. A correction is the sentence that gets folded.

## Public API

Other apps call Pi GPT on the same port. The page, `GET /health`, and `POST /v1/chat/completions` stay open on the LAN and do not send a key. Setting the key does not lock that page.

`PI_GPT_API_KEY` is the key. On pi4, `install.sh` creates `~/.config/pi-pair/pi-gpt-api.env` with mode 600 and points the user unit at it with `EnvironmentFile=`. Write one line, `PI_GPT_API_KEY=...`, in that file. The installer leaves the value empty and does not copy a key into git or into the unit. A drop-in that uses `Environment=PI_GPT_API_KEY=...` instead must itself be mode 600. The empty example is `pi-pair/configs/runtime/pi-gpt-api.env.example`, and the drop-in template is `pi-pair/configs/runtime/pi-pair.service.d/pi-gpt-api.conf`. If the variable is unset, `POST /api/chat` and `GET /api/health` return 503. A missing or wrong key returns 401. Send the value as `Authorization: Bearer <key>` or as `X-API-Key: <key>`. Do not put it in the query string. `GET /openapi.json` (and `/swagger.json`) and `GET /docs` (and `/swagger`) are readable without the key so a caller can see that scheme. `/docs` is the Swagger UI. It loads the OpenAPI document from this router. The same page lists the auth header and the routes when the Swagger script cannot be fetched. `/api/chat` and `/api/health` send `Access-Control-Allow-Origin` only when the browser Origin host matches this request's `Host`, or when that origin is listed in `PI_PAIR_CORS_ORIGINS`. A missing or other Origin gets no allow-origin header on those routes. The page and `/v1` stay open to any origin.

`POST /api/chat` takes an OpenAI `messages` list. `stream` is optional. `mode` is optional and is one of `flash`, `low`, `medium`, or `high`.

If `mode` is omitted, the model is Flash. Flash is the fleet checkpoint: `qwen3:0.6b` on pi4, unless `MESH_MODEL` names another tag. A `model` field in the body does not pick a different checkpoint. `low`, `medium`, and `high` stay on Flash and choose the thinking level, the same budgets as the page. Low is a direct answer of 64 tokens at temperature 0.7. Medium is a direct answer of 384 tokens at temperature 0.7. High thinks inside a 192-token or 25-second cap and then answers in 768 tokens at temperature 0.6. Omitted mode uses the medium level. Completions put reasoning in `reasoning_content`, never in `content`. The Thought for Ns panel is omitted when that field is empty. Flash here is that model name. It is not flash attention, which stays off on this CPU.

The JSON body names the public model in `model` (`flash`) and the selected mode in `mode`. `checkpoint` is the Ollama tag. A stored sentence still has `pi_model` `canned`. A generated sentence still has the checkpoint in `pi_model`. Pins of pi2 or pi3 are still refused. A miss is still generated only on pi4.

`POST /api/chat` uses the same inference cap as the page. When every slot is already decoding, the request waits in a queue of 8 for up to 60 seconds. The page shows `Waiting for a free slot…`. A full queue or a wait that runs out returns HTTP 503 with one short line and does not name the board. An exact map hit does not take a slot.

```bash
curl -sS http://127.0.0.1:18080/api/chat \
  -H 'content-type: application/json' \
  -H 'authorization: Bearer YOUR_KEY' \
  -d '{"messages":[{"role":"user","content":"status"}]}'
```

`GET /api/health` is the `/health` snapshot with `public_model` and `default_mode` set to `flash`.

```bash
curl -sS http://127.0.0.1:18080/openapi.json
curl -sS http://127.0.0.1:18080/api/health -H 'authorization: Bearer YOUR_KEY'
```

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
| `PI_PAIR_OLLAMA` | `http://127.0.0.1:11434` | Local Ollama origin for the Pro preload on the pi4 brain. `OLLAMA_HOST` is the fallback. |
| `PI_PAIR_ADAPTERS` | `adapters/` | Manifest directory |
| `PI_PAIR_TRAIN_CONFIG` | `configs/train/sft_canned.yaml` | Run file |
| `MESH_MODEL` | `qwen3:0.6b` | Name shown on the page. A LAN chat with no mode still runs Flash from `configs/runtime/inference_pi4.json`. |
| `PI_PAIR_MODE` | `flash` | `mesh-hello.sh` only. `pro` opts that probe into `qwen3:1.7b`. |
| `PI_GPT_API_KEY` | unset | Key for `POST /api/chat` and `GET /api/health`. Unset closes those two routes. The page does not use it. |
| `PI_PAIR_CORS_ORIGINS` | unset | Extra browser origins for `/api/*`, comma-separated. The request host is allowed without this. |
| `PI_PAIR_SLOTS` | `ollama_num_parallel` (2) | In-flight generations on pi4. Clamped to 1–4. Unset follows `configs/runtime/inference_pi4.json`, the same number `install.sh` writes as `OLLAMA_NUM_PARALLEL`. When the cap is full, up to 8 more chats wait about 60 seconds and the page shows a waiting line. A full queue returns HTTP 503 with a short message. Flash and Pro share this cap. `POST /api/chat` uses this same cap. |
| `PI_PAIR_HEALTH_TTL` | `2.5` | Seconds to cache peer probes |

Ollama's runner log for the previous Flash tag shows the cache, not a second copy of the weights: 24 MiB of key/value cache at one sequence, 48 MiB at two, 96 MiB at four, each sequence still `num_ctx` 2048. Those cache figures are not a Qwen3 measurement. A same-settings run of that previous tag kept the Ollama process tree under 1 GB at four sequences. The shipped cap is 2. Four sequences were too slow on pi4 (p95 34.9s), and `install.sh` writes that cap of 2 into the Ollama drop-in so a deploy does not put the board back on 4. Re-measure on pi4 after the drop-in is installed and the model is loaded: `python3 scripts/bench/concurrent.py --url http://127.0.0.1:18080 --n 2 --rounds 5`. That prints p50, p95, and the peak resident set of the `ollama` process tree. Direct to the model server is the same script with `--ollama http://127.0.0.1:11434`.

