# adapters/

`active/manifest.json` is written by a successful train job. It names the canned map hash and the base model that stays in Ollama on pi4. It is not a weight file.

`staging/` is empty between jobs. A failed gate does not promote.

Chat weights are not stored in this directory. They live in pi4's Ollama store. Raw train shards are not stored here either.
