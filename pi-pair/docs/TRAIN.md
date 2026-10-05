# Train then delete

Run this on pi3 (`PI_PAIR_ROLE=dataset`). pi4 and pi2 refuse the job.

```bash
python3 scripts/data/prepare_queue.py
python3 scripts/eval/run_gate.py
python3 scripts/lifecycle/post_train.py
```

`prepare_queue.py` only checks the registry and prints counts. `run_gate.py` checks the live map against the held-out file and does not edit it. `post_train.py` moves `data/train/pending/queue.jsonl` to `data/train/active/`, writes `data/prepared/<id>.jsonl`, folds new pairs into the canned map, promotes `adapters/active/manifest.json`, deletes the shard and the prepared file, and writes a tombstone in `data/train/done/` with no prompt text. A queued row may also carry `vote` (`up` or `down`) and `correction`. A down vote with no correction is not folded. A correction replaces the stored sentence for that line.

`scripts/lifecycle/delete_shards.py` removes leftovers in `data/train/active` and `data/prepared`. It leaves `data/train/pending` alone.

`scripts/train/run_sft.py` calls the same cycle as `post_train.py`. A fold that kept the raw shard would violate the disk rule, so the script does not offer that split.

The YAML file `configs/train/sft_canned.yaml` must name datasets that exist in `dataset_info.json`. An unknown name aborts before the queue moves.

The manifest records `weights: pi4-ollama`. No weight file is written here. The Ollama model stays on pi4.

Before the raw shard is deleted, voted rows are hashed into `data/train/public/labels.jsonl`. That file is the public copy. `python3 scripts/data/sync_mesh_labels.py` uploads it when `HF_TOKEN` is set. The token is not stored in git. pi4 does not run this script.
