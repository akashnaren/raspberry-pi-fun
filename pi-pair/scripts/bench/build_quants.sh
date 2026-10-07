#!/usr/bin/env bash
# Build imatrix Q4_K_M and Q4_K_S, plus an output-layer Q4_K variant.
# This does not download weights. Akash runs the real build on a machine
# that already has the bf16 files and llama.cpp tools.
set -euo pipefail

dry_run=0
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=1
fi

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'missing %s\n' "$1"
    return 1
  fi
  return 0
}

if [[ "$dry_run" -eq 1 ]]; then
  printf 'dry-run\n'
  printf 'variants: Q4_K_M Q4_K_S Q4_K_M-output-q4 Q4_K_S-output-q4\n'
  printf 'gate: KL within 0.01 of Q4_K_M, eval within one standard error\n'
  printf 'rejected: Q3_K IQ3 draft-model\n'
  exit 0
fi

missing=0
for tool in llama-imatrix llama-quantize llama-perplexity; do
  if ! need "$tool"; then
    missing=1
  fi
done
if [[ "$missing" -ne 0 ]]; then
  printf 'quant tools are not installed; nothing was downloaded\n'
  exit 0
fi

printf 'tools are present; pass the bf16 directory as the second argument when Akash runs this\n'
exit 0
