#!/usr/bin/env bash
# One-liner style. Must run as root. Use SSH with TTY for BOT_TOKEN/OWNER_ID.
#
# Prefer PIPE (works everywhere; sudo + process substitution often breaks /dev/fd):
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/BRANCH/install.sh | sudo bash -s --
#   curl -fsSL .../install.sh | sudo bash -s -- /opt/telegram-sales-bot main
#
# If already root, drop sudo:
#   curl -fsSL .../install.sh | bash -s -- /root/telegram-sales-bot main
#
# Optional env: REPO=owner/name  BRANCH=main  FRESH_DROP_DB=1  SAKA_BOT_UNIT=name.service

set -euo pipefail

REPO="${REPO:-diakoronin/Telegram-Auto-Pro-Full-Activated}"
INSTALL_DIR="${1:-${INSTALL_DIR:-/root/telegram-sales-bot}}"
BRANCH="${2:-${BRANCH:-main}}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root, for example:"
  echo "  sudo bash <(curl -Ls https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.sh)"
  echo "  sudo bash <(curl -Ls https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.sh) ${INSTALL_DIR} ${BRANCH}"
  exit 1
fi

BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
echo "[install] repo=${REPO} branch=${BRANCH} dir=${INSTALL_DIR}"
echo "[install] running fresh-install from ${BASE}/scripts/fresh-install.sh ..."
curl -fsSL "${BASE}/scripts/fresh-install.sh" | bash -s -- "$INSTALL_DIR" "$BRANCH"
