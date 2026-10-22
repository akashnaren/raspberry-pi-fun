#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAIR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=pi-pair/ci/pi3_ssh.sh
source "${SCRIPT_DIR}/pi3_ssh.sh"

usage() {
  cat <<'EOF'
Usage: restart_pi3.sh [--dry-run]

  After deploy_pi3.sh, restart the pi3 user unit once /health is idle,
  then wait until commit matches GITHUB_SHA.

  --dry-run   Print the plan and exit 0. No SSH. Secrets not required.

Examples:
  bash pi-pair/ci/restart_pi3.sh --dry-run
  GITHUB_SHA=0123456789abcdef0123456789abcdef01234567 bash pi-pair/ci/restart_pi3.sh
EOF
}

EXAMPLE="GITHUB_SHA=0123456789abcdef0123456789abcdef01234567 bash pi-pair/ci/restart_pi3.sh"

parse_expect() {
  PYTHONPATH="${PAIR_DIR}${PYTHONPATH:+:${PYTHONPATH}}" python3 -c \
    'from pair.core.stamp import parse_commit; import sys; sys.stdout.write(parse_commit(sys.argv[1]))' \
    "$1"
}

if [[ $# -gt 1 ]]; then
  usage >&2
  exit 2
fi

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --dry-run)
    if [[ -n "${GITHUB_SHA:-}" ]]; then
      expect="$(parse_expect "${GITHUB_SHA}")"
      if [[ "${expect}" == "unknown" ]]; then
        echo "restart_pi3: GITHUB_SHA is not a commit stamp. Example: ${EXAMPLE}" >&2
        exit 1
      fi
    fi
    echo "restart_pi3: dry-run; not restarting"
    if [[ -n "${GITHUB_SHA:-}" ]]; then
      echo "expect_commit: ${expect}"
    fi
    exit 0
    ;;
  "")
    ;;
  *)
    echo "restart_pi3: unknown argument: $1" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ -z "${GITHUB_SHA:-}" ]]; then
  echo "restart_pi3: GITHUB_SHA is required. Example: ${EXAMPLE}" >&2
  exit 1
fi
EXPECT="$(parse_expect "${GITHUB_SHA}")"
if [[ "${EXPECT}" == "unknown" ]]; then
  echo "restart_pi3: GITHUB_SHA is not a commit stamp. Example: ${EXAMPLE}" >&2
  exit 1
fi

pi3_require_target restart_pi3
pi3_open_ssh restart_pi3

read_health() {
  local body parsed
  if ! body="$("${SSH[@]}" "${PI3_SSH_USER}@${PI3_SSH_HOST}" \
      "curl -sS --max-time 5 http://127.0.0.1:18080/health")"; then
    return 1
  fi
  if ! parsed="$(printf '%s' "${body}" | PYTHONPATH="${PAIR_DIR}${PYTHONPATH:+:${PYTHONPATH}}" python3 -c '
import json
import sys

raw = sys.stdin.read()
try:
    doc = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit(1)
if not isinstance(doc, dict):
    raise SystemExit(1)

def as_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return ""
    return str(value)

ok = "1" if doc.get("ok") is True else "0"
commit = doc.get("commit")
if not isinstance(commit, str):
    commit = ""
sys.stdout.write(
    "%s\n%s\n%s\n%s\n"
    % (ok, commit, as_int(doc.get("in_flight")), as_int(doc.get("waiting")))
)
')"; then
    return 1
  fi
  HEALTH_OK="$(printf '%s\n' "${parsed}" | sed -n '1p')"
  HEALTH_COMMIT="$(printf '%s\n' "${parsed}" | sed -n '2p')"
  HEALTH_IN_FLIGHT="$(printf '%s\n' "${parsed}" | sed -n '3p')"
  HEALTH_WAITING="$(printf '%s\n' "${parsed}" | sed -n '4p')"
}

last_in=""
last_wait=""
idle=0
deadline=$((SECONDS + 60))
while [[ "${SECONDS}" -le "${deadline}" ]]; do
  if read_health; then
    last_in="${HEALTH_IN_FLIGHT}"
    last_wait="${HEALTH_WAITING}"
    if [[ "${HEALTH_IN_FLIGHT}" == "0" && "${HEALTH_WAITING}" == "0" ]]; then
      idle=1
      break
    fi
  fi
  if [[ "${SECONDS}" -ge "${deadline}" ]]; then
    break
  fi
  sleep 2
done

if [[ "${idle}" -ne 1 ]]; then
  echo "restart_pi3: idle wait elapsed in_flight=${last_in} waiting=${last_wait} restarting anyway" >&2
fi

# shellcheck disable=SC2016 # id -u has to expand on the Pi
"${SSH[@]}" "${PI3_SSH_USER}@${PI3_SSH_HOST}" \
  'XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user restart pi-pair.service'

post=$((SECONDS + 120))
while [[ "${SECONDS}" -le "${post}" ]]; do
  if read_health && [[ "${HEALTH_OK}" == "1" && "${HEALTH_COMMIT}" == "${EXPECT}" ]]; then
    echo "restart_pi3: ok commit=${EXPECT}"
    exit 0
  fi
  if [[ "${SECONDS}" -ge "${post}" ]]; then
    break
  fi
  sleep 2
done
echo "restart_pi3: timed out waiting for commit ${EXPECT}" >&2
exit 1
