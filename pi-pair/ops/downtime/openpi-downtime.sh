#!/usr/bin/env bash
# OpenPi nightly downtime switch. Window: 01:00-07:00 America/Los_Angeles.
# usage: openpi-downtime.sh auto|down|up|status   (timers + boot run "auto")
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SC="systemctl --user"
APP=pi-pair.service
BANNER=openpi-banner.service
log() { echo "openpi-downtime: $*"; logger -t openpi-downtime "$*" 2>/dev/null || true; }

in_window() { local h; h=$(TZ=America/Los_Angeles date +%-H); [ "$h" -ge 1 ] && [ "$h" -lt 7 ]; }

down() {
  if $SC is-active --quiet "$BANNER" && ! $SC is-active --quiet "$APP"; then log "already down"; return 0; fi
  $SC stop "$APP" || true
  for _ in $(seq 1 20); do ss -ltn 'sport = :18080' | grep -q LISTEN || break; sleep 0.5; done
  $SC start "$BANNER"
  log "down: $APP stopped, banner up"
}

up() {
  if $SC is-active --quiet "$APP" && ! $SC is-active --quiet "$BANNER"; then log "already up"; return 0; fi
  $SC stop "$BANNER" || true
  for _ in $(seq 1 20); do ss -ltn 'sport = :18080' | grep -q LISTEN || break; sleep 0.5; done
  $SC start "$APP"
  for _ in $(seq 1 60); do
    if curl -fsS -m 3 -o /dev/null http://127.0.0.1:18080/health; then log "up: $APP healthy"; return 0; fi
    sleep 2
  done
  log "up: $APP started but /health not ok after 120s"; return 1
}

case "${1:-auto}" in
  auto) if in_window; then down; else up; fi ;;
  down) down ;;
  up) up ;;
  status) echo "window=$(in_window && echo in || echo out) app=$($SC is-active $APP || true) banner=$($SC is-active $BANNER || true)" ;;
  *) echo "usage: $0 auto|down|up|status" >&2; exit 2 ;;
esac
