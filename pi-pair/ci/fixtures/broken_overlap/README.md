# broken_overlap

Negative fixture. The held-out eval `input` is copied from `canned/canned_seed.jsonl` on purpose.

`validate_data_stack.py --data` on this directory must exit non-zero with error code `eval_overlaps_canned`.

Do not point the CI data-stack job here. The job validates `pi-pair/data`. Deploy excludes `ci/fixtures/` so this tree is not synced to pi3.
