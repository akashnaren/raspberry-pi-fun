# Sources

The directory split in this repo follows the 2026-10-02 data-stack note (canned map, bounded queue, prepared artifacts, adapter manifests, held-out gate) and a reading of eight training codebases. Train-then-delete is a fleet rule. Those projects compact or stage data; they do not wipe the raw corpus as a built-in final step.

| Project | URL |
| --- | --- |
| Axolotl | https://github.com/axolotl-ai-cloud/axolotl |
| AllenAI Open-Instruct | https://github.com/allenai/open-instruct |
| LLaMA-Factory | https://github.com/hiyouga/LLaMA-Factory |
| Hugging Face TRL | https://github.com/huggingface/trl |
| LitGPT | https://github.com/Lightning-AI/litgpt |
| nanoGPT | https://github.com/karpathy/nanoGPT |
| TinyLlama | https://github.com/jzhang38/TinyLlama |
| Unsloth | https://github.com/unslothai/unsloth |

Synthetic seeds, not live queues:

- https://huggingface.co/datasets/akashnaren/pi-flywheel-canned
- https://huggingface.co/datasets/akashnaren/pi-flywheel-sft-seed
- https://huggingface.co/datasets/akashnaren/pi-flywheel-eval
- https://huggingface.co/datasets/akashnaren/pi-mesh-labels (HMAC votes only, not created until Akash approves it; raw chat stays on pi3)

Qwen2.5-0.5B-Instruct layer sizes cited in the router README come from the published config: https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct

Fetched supporting notes, 2026-10-02: Axolotl `AGENTS.md`, Open-Instruct README, LLaMA-Factory `data/README.md` and `examples/README.md`, TRL CLI docs, LitGPT README, nanoGPT README, TinyLlama README.
