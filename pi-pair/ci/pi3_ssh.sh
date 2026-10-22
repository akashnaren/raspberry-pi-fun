#!/usr/bin/env bash

pi3_require_target() {
  local prefix="$1"
  local missing=()
  local host_lc
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
    echo "${prefix}: missing required secrets: ${missing[*]}" >&2
    echo "Set them on the GitHub Environment named production. Refusing to deploy." >&2
    exit 1
  fi

  case "${PI3_SSH_KEY}" in
    /*|~/*)
      echo "${prefix}: PI3_SSH_KEY must be the private key text, not a path. Refusing to deploy." >&2
      exit 1
      ;;
  esac
  case "${PI3_SSH_KEY}" in
    *"PRIVATE KEY"*) ;;
    *)
      echo "${prefix}: PI3_SSH_KEY does not look like a private key. Refusing to deploy." >&2
      exit 1
      ;;
  esac

  host_lc="$(printf '%s' "${PI3_SSH_HOST}" | tr '[:upper:]' '[:lower:]')"
  case "${host_lc}" in
    *pi2*|*pi4*|10.0.0.180|10.0.0.166)
      echo "${prefix}: refusing host ${PI3_SSH_HOST}. Sync targets pi3 only. Generation stays on pi4. Health stays on pi2." >&2
      exit 1
      ;;
  esac

  PORT="${PI3_SSH_PORT:-22}"
  if [[ ! "${PORT}" =~ ^[0-9]+$ ]] || [[ "${PORT}" -lt 1 || "${PORT}" -gt 65535 ]]; then
    echo "${prefix}: PI3_SSH_PORT must be a TCP port" >&2
    exit 1
  fi
}

pi3_open_ssh() {
  local prefix="$1"
  if ! command -v ssh-keyscan >/dev/null 2>&1; then
    echo "${prefix}: ssh-keyscan is not installed. Refusing to deploy." >&2
    exit 1
  fi

  umask 077
  tmp="$(mktemp -d)"
  cleanup() {
    # shellcheck disable=SC2317 # trap runs this on exit
    rm -rf "${tmp}"
  }
  trap cleanup EXIT

  printf '%s\n' "${PI3_SSH_KEY}" > "${tmp}/id"
  chmod 600 "${tmp}/id"

  if ! ssh-keyscan -p "${PORT}" -T 10 -H "${PI3_SSH_HOST}" > "${tmp}/known_hosts" 2>"${tmp}/keyscan.err"; then
    echo "${prefix}: ssh-keyscan failed for ${PI3_SSH_HOST}:${PORT}. Refusing to disable host-key checks." >&2
    exit 1
  fi
  if [[ ! -s "${tmp}/known_hosts" ]]; then
    echo "${prefix}: ssh-keyscan returned no keys for ${PI3_SSH_HOST}:${PORT}. Refusing to disable host-key checks." >&2
    exit 1
  fi

  declare -gax SSH=(
    ssh
    -i "${tmp}/id"
    -p "${PORT}"
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o "UserKnownHostsFile=${tmp}/known_hosts"
    -o ConnectTimeout=15
  )
}
