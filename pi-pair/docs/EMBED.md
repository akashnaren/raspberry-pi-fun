# Embed on pi3

pi3 keeps `snowflake-arctic-embed:xs` on loopback for the train fold and unloads it before OCR. pi4 and pi2 never hold an embed model.

The tag is about 22M parameters and 384 dimensions. It is not a chat model. Nothing in chat, search, image cards, memory, or decode calls it. The only caller in this version is the pi3 train fold, and it calls pi3's own `POST /tools/embed` on `127.0.0.1:18080`.

## Where it runs

| Board | Embed |
| --- | --- |
| pi3 | On demand, loopback Ollama only. Unloaded before tesseract or pdftoppm. |
| pi4 | Not installed. `/tools/embed` is refused. Chat stays on Qwen3 Flash and Pro. |
| pi2 | No Ollama. `/tools/embed` answers `skipped: off`. |

`install.sh` on pi3 removes every `qwen2.5` tag and every Arctic tag other than `:xs`, then pulls `:xs`. It does not preload the tag. On pi4 it removes every `snowflake-arctic-embed` tag, including `:m` and `:xs`, and never pulls one. pi2 does not touch Ollama.

If `ollama` is missing on pi3, the installer says Arctic `:xs` is optional and the train fold stays exact-only. It does not print a sudo install command.

## Loopback unit

On the dataset role the installer writes `~/.config/systemd/user/ollama-lan.service.d/pi3-embed.conf`:

```
[Service]
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_QUEUE=2
Environment=OLLAMA_KEEP_ALIVE=0
```

After install on pi3, run:

```bash
systemctl --user daemon-reload && systemctl --user restart ollama-lan
systemctl --user restart pi-pair
```

`127.0.0.1:11434` keeps this Ollama off the LAN, so pi4 cannot use it as a generator. `OLLAMA_KEEP_ALIVE=0` drops the runner when a request finishes. A call may also ask to keep it for 30 seconds, then it unloads.

## OCR

Loading the tag and running OCR at the same time does not fit comfortably on a 1GB board. Before tesseract or pdftoppm starts, pi3 stops new embed calls, waits up to 1.5 seconds for an in-flight embed, then unloads the tag (budget 2 seconds). OCR then runs as it does today. A second OCR page does not unload again. The next embed loads the tag on demand. If memory available is under 250 MB, embed skips instead of loading.

`PI_PAIR_EMBED=0` turns the feature off. The fold then matches keys exactly, the same as before this tag existed.

## Train fold

`post_train` on pi3 embeds a new queue key and compares it with keys already in the map using cosine similarity. At or above the threshold in `configs/runtime/embed_pi3.json` (default 0.92 until that file is calibrated on pi3), the row is a paraphrase: no new key is added, and a correction updates the matched key. A down vote with no correction is still rejected. Held-out keys stay blocked. If the embed call fails, that row is exact-only.

Key vectors are cached in `data/canned/key_vectors.json` on pi3. That file is not served and is not part of the public sync.

Calibrate once on pi3, after deploy:

```bash
python3 scripts/eval/calibrate_paraphrase.py
```

Commit the generated `configs/runtime/embed_pi3.json` in a follow-up, or leave the 0.92 default.
