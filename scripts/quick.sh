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
    log "نسخه قبلی پیدا شد → به‌روزرسانی امن (توقف ربات، pull، pip، راه‌اندازی مجدد)"
    exec bash "$INSTALL_DIR/scripts/update.sh"
  fi

  if [[ -d "$INSTALL_DIR/.git" ]] && [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
    log "پوشه قدیمی با git → pull و بعد update"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" fetch origin || true
    git -C "$INSTALL_DIR" checkout "$BRANCH" 2>/dev/null || true
    git -C "$INSTALL_DIR" pull origin "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" pull --ff-only || die "git pull failed"
    if [[ -f "$INSTALL_DIR/scripts/update.sh" ]]; then
      exec bash "$INSTALL_DIR/scripts/update.sh"
    fi
    die "بعد از pull هنوز scripts/update.sh نیست — شاخه را عوض کنید یا دستی کلون تازه بزنید"
  fi

  if [[ -e "$INSTALL_DIR" ]] && [[ ! -d "$INSTALL_DIR/.git" ]]; then
    die "مسیر $INSTALL_DIR وجود دارد ولی git clone نیست. آن را پاک/تغییر نام دهید یا مسیر دیگری بدهید."
  fi

  INSTALL_URL="$(repo_to_raw_install_url "$REPO_URL" "$BRANCH")"
  log "نصب تازه از: $INSTALL_URL"
  log "(نیاز به sudo و TTY برای BOT_TOKEN و OWNER_ID)"
  curl -fsSL "$INSTALL_URL" | sudo bash -s -- "$INSTALL_DIR" "$BRANCH"
}

main "$@"
