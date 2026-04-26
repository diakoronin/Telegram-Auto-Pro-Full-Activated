#!/usr/bin/env bash
# One-liner style. Must run as root. Use SSH with TTY for BOT_TOKEN/OWNER_ID.
#
# Pipe (recommended):
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/REF/install.sh | bash -s --
#   curl -fsSL .../install.sh | bash -s -- /root/telegram-sales-bot main
#
# If the 2nd argument is omitted and env BRANCH is unset, tries git ref "main" first;
# if scripts/fresh-install.sh is missing on main (404), uses cursor/telegram-sales-bot-security-712f.
#
# Optional env: REPO=owner/name  BRANCH=ref  FRESH_DROP_DB=1  SAKA_BOT_UNIT=name.service

set -euo pipefail

REPO="${REPO:-diakoronin/Telegram-Auto-Pro-Full-Activated}"
INSTALL_DIR="${1:-${INSTALL_DIR:-/root/telegram-sales-bot}}"
FEATURE_FALLBACK="cursor/telegram-sales-bot-security-712f"

http_code() {
  curl -sS -o /dev/null -w "%{http_code}" -L "https://raw.githubusercontent.com/${REPO}/$1/scripts/fresh-install.sh" 2>/dev/null || echo "000"
}

pick_branch() {
  if [[ -n "${2:-}" ]]; then
    echo "$2"
    return
  fi
  if [[ -n "${BRANCH:-}" ]]; then
    echo "$BRANCH"
    return
  fi
  local c
  c="$(http_code main)"
  if [[ "$c" == "200" ]]; then
    echo "main"
    return
  fi
  c="$(http_code "$FEATURE_FALLBACK")"
  if [[ "$c" == "200" ]]; then
    echo "$FEATURE_FALLBACK"
    return
  fi
  echo "$FEATURE_FALLBACK"
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root, for example:"
  echo "  curl -fsSL https://raw.githubusercontent.com/${REPO}/REF/install.sh | sudo bash -s --"
  exit 1
fi

BRANCH="$(pick_branch "$@")"
BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
echo "[install] repo=${REPO} branch=${BRANCH} dir=${INSTALL_DIR}"
echo "[install] running fresh-install from ${BASE}/scripts/fresh-install.sh ..."
curl -fsSL "${BASE}/scripts/fresh-install.sh" | bash -s -- "$INSTALL_DIR" "$BRANCH"
