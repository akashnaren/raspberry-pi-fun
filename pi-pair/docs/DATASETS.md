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

Public votes are a different dataset from these seeds. pi3 uploads SHA-256 hashes and the vote to `akashnaren/pi-mesh-labels` when `HF_TOKEN` is set. Raw prompt and answer text stay off that repo. `KAGGLE_API_TOKEN` is an optional stub that runs only after the Hugging Face upload succeeds.
