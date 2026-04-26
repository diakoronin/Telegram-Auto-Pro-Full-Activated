#!/usr/bin/env bash
# One command: update existing clone OR full install (install.sh).
# Safe for servers with an old copy: stops bot before pull (via update.sh).
#
# One-liner (SSH with TTY for fresh install prompts):
#   curl -fsSL "https://raw.githubusercontent.com/USER/REPO/BRANCH/scripts/quick.sh" | bash -s -- ~/telegram-sales-bot BRANCH
#
# Env:
#   REPO_URL — default GitHub HTTPS clone URL (used to build raw URL for install.sh)
#   INSTALL_DIR / BRANCH — optional if not passed as args

set -euo pipefail

INSTALL_DIR="${1:-${INSTALL_DIR:-$HOME/telegram-sales-bot}}"
BRANCH="${2:-${BRANCH:-main}}"
REPO_URL="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"

log() { printf '%s\n' "[quick] $*"; }
die() { printf '%s\n' "[quick] ERROR: $*" >&2; exit 1; }

repo_to_raw_install_url() {
  local url="$1" br="$2"
  local r="${url#https://github.com/}"
  r="${r#http://github.com/}"
  r="${r%.git}"
  [[ -n "$r" ]] || die "Bad REPO_URL: $url"
  printf 'https://raw.githubusercontent.com/%s/%s/scripts/install.sh' "$r" "$br"
}

main() {
  log "INSTALL_DIR=$INSTALL_DIR  BRANCH=$BRANCH"

  if [[ -f "$INSTALL_DIR/scripts/update.sh" ]]; then
    log "Existing install found -> safe update (stop bot, pull, pip, restart)"
    exec bash "$INSTALL_DIR/scripts/update.sh"
  fi

  if [[ -d "$INSTALL_DIR/.git" ]] && [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
    log "Git repo without update.sh -> pull then update"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" fetch origin || true
    git -C "$INSTALL_DIR" checkout "$BRANCH" 2>/dev/null || true
    git -C "$INSTALL_DIR" pull origin "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" pull --ff-only || die "git pull failed"
    if [[ -f "$INSTALL_DIR/scripts/update.sh" ]]; then
      exec bash "$INSTALL_DIR/scripts/update.sh"
    fi
    die "After pull, scripts/update.sh is still missing. Use another branch or clone fresh."
  fi

  if [[ -e "$INSTALL_DIR" ]] && [[ ! -d "$INSTALL_DIR/.git" ]]; then
    die "Path $INSTALL_DIR exists but is not a git clone. Remove/rename it or use a different INSTALL_DIR."
  fi

  INSTALL_URL="$(repo_to_raw_install_url "$REPO_URL" "$BRANCH")"
  log "Fresh install from: $INSTALL_URL"
  log "(requires sudo and a TTY for BOT_TOKEN and OWNER_ID prompts)"
  curl -fsSL "$INSTALL_URL" | sudo bash -s -- "$INSTALL_DIR" "$BRANCH"
}

main "$@"
