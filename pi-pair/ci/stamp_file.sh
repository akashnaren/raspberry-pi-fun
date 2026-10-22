#!/usr/bin/env bash
# Sourced by deploy_pi3.sh and install.sh.

stamp_write() {
  local dest_dir="$1"
  local text="$2"
  local dest tmp
  dest="${dest_dir}/DEPLOYED_SHA"
  tmp="$(mktemp "${dest_dir}/DEPLOYED_SHA.tmp.XXXXXX")"
  printf '%s\n' "${text}" > "${tmp}"
  chmod 0644 "${tmp}"
  mv -f "${tmp}" "${dest}"
}
