# data/

`dataset_info.json` is the registry. Train configs name its keys, not loose files.

`canned/canned_map.json` is what the router serves. `canned/canned_seed.jsonl` is the synthetic seed the map was built from. `seed/` holds SFT, preference, held-out eval, and JSON schemas. `train/pending` is the bounded miss queue on pi3. `train/active` and `prepared/` exist only during a job and are deleted when it finishes. `train/done/` keeps tombstones without the raw text.

Do not copy `train/` or `prepared/` onto pi4. pi2 may mirror `canned/canned_map.json` and nothing else in this tree.
