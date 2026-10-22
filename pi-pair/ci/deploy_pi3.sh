#!/usr/bin/env bash
# Idempotent sync of pi-pair onto pi3 (dataset, canned map, train-then-delete).
# The CD workflow joins Tailscale with production secret TS_AUTHKEY before this runs.
# This script still requires PI3_SSH_* and still refuses pi2 and pi4.
#
# Does not run install.sh, does not pull models, does not restart systemd,
# and does not copy shards, prepared artifacts, adapters, or GGUF weights.
# Generation stays on pi4. Health stays on pi2. A host that looks like pi2
# or pi4 is refused.
#
# Required environment for a real sync and for --dry-run (GitHub Environment
# "production"; never invent a key):
#   PI3_SSH_HOST   Tailscale name or address of pi3. Documented name: rpi-pi3.
#   PI3_SSH_USER   SSH user on pi3.
#   PI3_SSH_KEY    Private key text, including the BEGIN/END lines. Not a path.
# Optional:
#   PI3_SSH_PORT   Default 22.
#   PI3_PAIR_DIR   Remote directory. Default: pi-pair (relative to $HOME).
#                  The last path segment must be "pi-pair" so --delete cannot
#                  wipe an unrelated directory.
#
# --plan prints the sync and exits. It does not read a requirement for secrets
# and does not open SSH. CD does not pass --plan.
# --dry-run is rsync -n. It still fails closed when secrets are missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAIR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${PAIR_DIR}/data"
EXCLUDES="${SCRIPT_DIR}/rsync-excludes.txt"
# shellcheck source=pi-pair/ci/stamp_file.sh
source "${SCRIPT_DIR}/stamp_file.sh"
# shellcheck source=pi-pair/ci/pi3_ssh.sh
source "${SCRIPT_DIR}/pi3_ssh.sh"

DRY_RUN=0
PLAN=0

usage() {
  cat <<'EOF'
Usage: deploy_pi3.sh [--dry-run | --plan]

  --dry-run   rsync -n. Requires PI3_SSH_HOST, PI3_SSH_USER, and PI3_SSH_KEY.
  --plan      Print the sync plan and exit. No SSH, secrets not required.

See pi-pair/docs/DEPLOY.md for the production environment secret names.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --plan) PLAN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "deploy_pi3: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${PLAN}" -eq 1 && "${DRY_RUN}" -eq 1 ]]; then
  echo "deploy_pi3: pass only one of --plan or --dry-run" >&2
  exit 2
fi

if [[ ! -f "${EXCLUDES}" ]]; then
  echo "deploy_pi3: missing ${EXCLUDES}" >&2
  exit 1
fi

python3 "${SCRIPT_DIR}/validate_data_stack.py" \
  --data "${DATA_DIR}" \
  --report "${SCRIPT_DIR}/out/data-stack-report.json"

DEPLOY_SHA="${GITHUB_SHA:-}"
if [[ -z "${DEPLOY_SHA}" ]]; then
  DEPLOY_SHA="$(git -C "${PAIR_DIR}" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ -z "${DEPLOY_SHA}" ]]; then
  echo "deploy_pi3: empty commit. Example: GITHUB_SHA=0123456789abcdef0123456789abcdef01234567 bash pi-pair/ci/deploy_pi3.sh" >&2
  exit 1
fi

print_plan() {
  local mode dest_host dest_user dest_dir
  if [[ "${PLAN}" -eq 1 ]]; then
    mode="plan (no ssh)"
  elif [[ "${DRY_RUN}" -eq 1 ]]; then
    mode="rsync --dry-run"
  else
    mode="sync"
  fi
  dest_host="${PI3_SSH_HOST:-<PI3_SSH_HOST>}"
  dest_user="${PI3_SSH_USER:-<PI3_SSH_USER>}"
  dest_dir="${PI3_PAIR_DIR:-pi-pair}"
  cat <<EOF
deploy_pi3 plan
  mode: ${mode}
  source: ${PAIR_DIR}/
  dest: ${dest_user}@${dest_host}:${dest_dir}/
  locks: generate=pi4 dataset_and_train=pi3 health=pi2 search=pi2 train_then_delete=yes weak_gen=no
  includes: router code, data/canned, data/seed, dataset_info.json
  DEPLOYED_SHA: ${DEPLOY_SHA}
  not executed: install.sh (no model pull on pi3), systemctl
EOF
  echo "  excludes:"
  sed 's/^/    /' "${EXCLUDES}"
}

if [[ "${PLAN}" -eq 1 ]]; then
  print_plan
  exit 0
fi

pi3_require_target deploy_pi3

DEST_DIR="${PI3_PAIR_DIR:-pi-pair}"
dest_base="${DEST_DIR%/}"
dest_base="${dest_base##*/}"
if [[ "${dest_base}" != "pi-pair" || "${DEST_DIR}" == *..* ]]; then
  echo "deploy_pi3: PI3_PAIR_DIR must be named pi-pair (got ${DEST_DIR}). Refusing --delete elsewhere." >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "deploy_pi3: rsync is not installed. Refusing to deploy." >&2
  exit 1
fi

pi3_open_ssh deploy_pi3

RSYNC_FLAGS=(-a --delete --human-readable --exclude-from "${EXCLUDES}")
if [[ "${DRY_RUN}" -eq 1 ]]; then
  RSYNC_FLAGS+=(-n --itemize-changes)
else
  RSYNC_FLAGS+=(-v)
fi

print_plan
remote_shell="$(printf '%q ' "${SSH[@]}")"
stamp_write "${PAIR_DIR}" "${DEPLOY_SHA}"
rsync "${RSYNC_FLAGS[@]}" -e "${remote_shell}" \
  "${PAIR_DIR}/" \
  "${PI3_SSH_USER}@${PI3_SSH_HOST}:${DEST_DIR}/"
