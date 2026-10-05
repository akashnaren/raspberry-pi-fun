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

DRY_RUN=0
PLAN=0

usage() {
  cat <<'EOF'
Usage: deploy_pi3.sh [--dry-run | --plan]

  --dry-run   rsync -n. Requires PI3_SSH_HOST, PI3_SSH_USER, and PI3_SSH_KEY.
  --plan      Print the sync plan and exit. No SSH, secrets not required.

See pi-pair/CI-CD.md for the production environment secret names.
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
  not executed: install.sh (no model pull on pi3), systemctl
EOF
  echo "  excludes:"
  sed 's/^/    /' "${EXCLUDES}"
}

if [[ "${PLAN}" -eq 1 ]]; then
  print_plan
  exit 0
fi

missing=()
if [[ -z "${PI3_SSH_HOST:-}" ]]; then
  missing+=("PI3_SSH_HOST")
fi
if [[ -z "${PI3_SSH_USER:-}" ]]; then
  missing+=("PI3_SSH_USER")
fi
if [[ -z "${PI3_SSH_KEY:-}" ]]; then
  missing+=("PI3_SSH_KEY")
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "deploy_pi3: missing required secrets: ${missing[*]}" >&2
  echo "Set them on the GitHub Environment named production. Refusing to deploy." >&2
  exit 1
fi

case "${PI3_SSH_KEY}" in
  /*|~/*)
    echo "deploy_pi3: PI3_SSH_KEY must be the private key text, not a path. Refusing to deploy." >&2
    exit 1
    ;;
esac
case "${PI3_SSH_KEY}" in
  *"PRIVATE KEY"*) ;;
  *)
    echo "deploy_pi3: PI3_SSH_KEY does not look like a private key. Refusing to deploy." >&2
    exit 1
    ;;
esac

host_lc="$(printf '%s' "${PI3_SSH_HOST}" | tr '[:upper:]' '[:lower:]')"
case "${host_lc}" in
  *pi2*|*pi4*|10.0.0.180|10.0.0.166)
    echo "deploy_pi3: refusing host ${PI3_SSH_HOST}. Sync targets pi3 only. Generation stays on pi4. Health stays on pi2." >&2
    exit 1
    ;;
esac

PORT="${PI3_SSH_PORT:-22}"
if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || [[ "${PORT}" -lt 1 || "${PORT}" -gt 65535 ]]; then
  echo "deploy_pi3: PI3_SSH_PORT must be a TCP port" >&2
  exit 1
fi

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
if ! command -v ssh-keyscan >/dev/null 2>&1; then
  echo "deploy_pi3: ssh-keyscan is not installed. Refusing to deploy." >&2
  exit 1
fi

umask 077
tmp="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp}"
}
trap cleanup EXIT

printf '%s\n' "${PI3_SSH_KEY}" > "${tmp}/id"
chmod 600 "${tmp}/id"

if ! ssh-keyscan -p "${PORT}" -T 10 -H "${PI3_SSH_HOST}" > "${tmp}/known_hosts" 2>"${tmp}/keyscan.err"; then
  echo "deploy_pi3: ssh-keyscan failed for ${PI3_SSH_HOST}:${PORT}. Refusing to disable host-key checks." >&2
  exit 1
fi
if [[ ! -s "${tmp}/known_hosts" ]]; then
  echo "deploy_pi3: ssh-keyscan returned no keys for ${PI3_SSH_HOST}:${PORT}. Refusing to disable host-key checks." >&2
  exit 1
fi

SSH=(
  ssh
  -i "${tmp}/id"
  -p "${PORT}"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=${tmp}/known_hosts"
  -o ConnectTimeout=15
)

RSYNC_FLAGS=(-a --delete --human-readable --exclude-from "${EXCLUDES}")
if [[ "${DRY_RUN}" -eq 1 ]]; then
  RSYNC_FLAGS+=(-n --itemize-changes)
else
  RSYNC_FLAGS+=(-v)
fi

print_plan
remote_shell="$(printf '%q ' "${SSH[@]}")"
rsync "${RSYNC_FLAGS[@]}" -e "${remote_shell}" \
  "${PAIR_DIR}/" \
  "${PI3_SSH_USER}@${PI3_SSH_HOST}:${DEST_DIR}/"
