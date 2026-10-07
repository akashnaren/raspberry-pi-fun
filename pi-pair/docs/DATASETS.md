# Datasets

Register a dataset in `data/dataset_info.json` before any train config names it. Paths in that file are relative to `data/`. The copies under `data/seed/` and `data/seed/schemas/` use paths relative to themselves and the same dataset names.

| Name | Stage | File |
| --- | --- | --- |
| `pi_flywheel_canned` | serve | `data/canned/canned_seed.jsonl` and the map `data/canned/canned_map.json` |
| `pi_flywheel_sft_chat` | sft | `data/seed/sft_seed/sft_chat.jsonl` |
| `pi_flywheel_sft_alpaca` | sft | `data/seed/sft_seed/sft_alpaca.jsonl` |
| `pi_flywheel_preference` | dpo | `data/seed/sft_seed/preference_pairs.jsonl` |
| `pi_flywheel_eval_heldout` | eval | `data/seed/eval_heldout/eval_heldout.jsonl` |

The router serves only the map. The JSONL files are the registered seeds. The miss queue is not a registered training corpus; it is an ephemeral shard under `data/train/pending/` and is deleted after a successful fold. Held-out inputs are a gate. They are not keys in the map and they are not folded in from the queue.

pi2 may store a read-only copy of the map. It does not get `data/train/`. pi4 does not get the queue or `data/prepared/`.

Public votes are a different dataset from these seeds. pi3 stores HMAC-SHA256 of the prompt and answer, keyed by `PI_PAIR_LABEL_PEPPER`, plus the vote. A bare SHA-256 is not written. The upload to `akashnaren/pi-mesh-labels` runs only when `HF_TOKEN` is set. Leave that token unset, and do not create the dataset, until Akash approves a public copy. Raw prompt and answer text stay off that repo. `KAGGLE_API_TOKEN` is an optional stub that runs only after the Hugging Face upload succeeds.

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

Those three repos are the synthetic seeds. Live queue text is not part of them. Hashed votes are a separate public dataset, https://huggingface.co/datasets/akashnaren/pi-mesh-labels , and that repo does not contain raw chat.

