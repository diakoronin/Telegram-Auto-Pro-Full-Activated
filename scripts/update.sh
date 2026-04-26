#!/usr/bin/env bash
# Safe update: stop bot (avoid Telegram getUpdates conflict), git pull, pip, restart systemd if it was running.
#
#   cd /root/telegram-sales-bot && bash scripts/update.sh
#   bash /path/to/repo/scripts/update.sh
#
# Env:
#   SKIP_SYSTEMD=1  — do not stop/start systemd (only pull + pip)
#   SKIP_PIP=1      — skip pip install

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

UNIT="${SYSTEMD_UNIT:-${SAKA_BOT_UNIT:-telegram-sales-bot.service}}"
MAIN="${ROOT}/main.py"

log() { printf '%s\n' "[update] $*"; }
die() { printf '%s\n' "[update] ERROR: $*" >&2; exit 1; }

run_sctl() {
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$@"
  else
    sudo systemctl "$@"
  fi
}

was_service_active() {
  run_sctl is-active --quiet "$UNIT" 2>/dev/null
}

stop_bot_processes() {
  # Stop systemd unit if active (single poller rule)
  if [[ "${SKIP_SYSTEMD:-}" != "1" ]] && command -v systemctl >/dev/null 2>&1; then
    if was_service_active; then
      log "Stopping $UNIT (was active)..."
      run_sctl stop "$UNIT" || true
      sleep 1
    fi
  fi
  # Kill manual runs of this repo (same bot token → TelegramConflictError)
  if pgrep -f "${ROOT}/main.py" >/dev/null 2>&1; then
    log "Stopping stray processes for ${ROOT}/main.py ..."
    pkill -f "${ROOT}/main.py" || true
    sleep 1
  fi
}

restart_service_if_needed() {
  if [[ "${SKIP_SYSTEMD:-}" == "1" ]]; then
    log "SKIP_SYSTEMD=1 — start the bot yourself: $ROOT/scripts/run.sh"
    return
  fi
  if [[ "$_UPDATE_HAD_ACTIVE_SERVICE" != "1" ]]; then
    log "Systemd unit was not active before update. Start manually:"
    log "  $ROOT/scripts/run.sh"
    log "  or: sudo systemctl start $UNIT"
    return
  fi
  if command -v systemctl >/dev/null 2>&1 && [[ -f "/etc/systemd/system/$UNIT" || -f "/lib/systemd/system/$UNIT" ]]; then
    log "Starting $UNIT..."
    run_sctl start "$UNIT" || die "systemctl start failed"
    sleep 1
    run_sctl is-active --quiet "$UNIT" && log "OK: $UNIT is active" || log "WARN: check: systemctl status $UNIT"
  else
    log "No systemd unit file for $UNIT — run: $ROOT/scripts/run.sh"
  fi
}

_UPDATE_HAD_ACTIVE_SERVICE=0

main() {
  log "ROOT=$ROOT"
  [[ -d .git ]] || die "Not a git clone"
  [[ -d .venv ]] || die "No .venv — run scripts/install.sh first"

  if [[ "${SKIP_SYSTEMD:-}" != "1" ]] && command -v systemctl >/dev/null 2>&1 && was_service_active; then
    _UPDATE_HAD_ACTIVE_SERVICE=1
  fi

  stop_bot_processes

  BR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  log "git pull (branch: $BR)..."
  git fetch origin "$BR" 2>/dev/null || git fetch origin
  git pull --ff-only origin "$BR" 2>/dev/null || git pull --ff-only || die "git pull failed (resolve conflicts manually)"

  if [[ "${SKIP_PIP:-}" != "1" ]]; then
    log "pip install -r requirements.txt ..."
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -q -U pip wheel setuptools
    pip install -q -r requirements.txt
  fi

  if [[ -f scripts/diagnose.sh ]]; then
    log "Running diagnose (non-fatal if DB/token not set)..."
    bash scripts/diagnose.sh "$ROOT" || true
  fi

  restart_service_if_needed
  log "Done."
}

main "$@"
