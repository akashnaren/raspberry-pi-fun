# data/

`dataset_info.json` is the registry. Train configs name its keys, not loose files.

`canned/canned_map.json` is what the router serves. `canned/canned_seed.jsonl` is the synthetic seed the map was built from. `seed/` holds SFT, preference, held-out eval, and JSON schemas. `train/pending` is the bounded miss queue on pi3. `train/active` and `prepared/` exist only during a job and are deleted when it finishes. `train/done/` keeps tombstones without the raw text.

Do not copy `train/` or `prepared/` onto pi4. pi2 may mirror `canned/canned_map.json` and nothing else in this tree. pi2 answers `POST /v1/search` and does not keep the queue. `train/public/labels.jsonl` on pi3 is HMAC-SHA256 votes keyed by `PI_PAIR_LABEL_PEPPER`, not bare SHA-256. Train-then-delete removes the raw queue and leaves that file. The Hugging Face upload stays off while `HF_TOKEN` is unset.

`BUILD_MANIFEST.json` records these seed counts and the role locks CI checks: generation is pi4 only, dataset and train are pi3 only, health and search are pi2 only, train-then-delete stays on, weak generation stays off. The counts are the checked-in seeds (canned 111, chat 20, alpaca 6, preference 8, held-out 15).

```bash
python3 pi-pair/ci/validate_data_stack.py --data pi-pair/data --report /tmp/data-stack-report.json
```

Negative check (must fail with `eval_overlaps_canned`):

```bash
python3 pi-pair/ci/validate_data_stack.py --data pi-pair/ci/fixtures/broken_overlap
```
