#!/usr/bin/env bash
# Interactive VPS manager for Telegram VPN sales bot.
# Usage: sudo ./bot-manager.sh   OR   ./bot-manager.sh (from repo root)

set -euo pipefail

ROOT="${SAKA_BOT_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
cd "$ROOT"
UNIT="${SYSTEMD_UNIT:-telegram-sales-bot.service}"
VENV="${ROOT}/.venv/bin/python"
MAIN="${ROOT}/main.py"

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[bot-manager] $*"; }

mask_env() {
  sed -E 's/^(BOT_TOKEN=).*/\1***/; s/(DATABASE_URL=.*:)([^@]+)(@)/\1***\3/; s/^(OWNER_ID=).*/\1***/' "${ROOT}/.env" 2>/dev/null || true
}

while true; do
  echo ""
  echo "========== Saka Bot Manager =========="
  echo "0) Exit"
  echo "1) Install / update deps (git pull + pip)"
  echo "2) Run DB migrations (start bot once / check_startup)"
  echo "3) Start bot (systemd)"
  echo "4) Stop bot"
  echo "5) Restart bot"
  echo "6) Status"
  echo "7) Logs (journalctl -n 100)"
  echo "8) Health (curl /health)"
  echo "9) View .env (masked)"
  echo "10) Security: .env permissions + gitignore check"
  echo "========================================"
  read -r -p "Choice: " choice || exit 0
  case "$choice" in
    0) exit 0 ;;
    1)
      [[ -d .git ]] || die "Not a git repo"
      git pull --ff-only || true
      [[ -d .venv ]] || python3 -m venv .venv
      .venv/bin/pip install -U pip
      .venv/bin/pip install -r requirements.txt
      log "pip done"
      ;;
    2)
      [[ -f .env ]] || die "Missing .env"
      "$VENV" "${ROOT}/scripts/check_startup.py" || true
      ;;
    3)
      sudo systemctl start "$UNIT" || die "start failed"
      ;;
    4)
      sudo systemctl stop "$UNIT" || true
      ;;
    5)
      sudo systemctl restart "$UNIT" || die "restart failed"
      ;;
    6)
      sudo systemctl status "$UNIT" --no-pager || true
      ;;
    7)
      sudo journalctl -u "$UNIT" -n 100 --no-pager || true
      ;;
    8)
      port=$(grep SUBSCRIPTION_BIND_PORT .env 2>/dev/null | cut -d= -f2 || echo 8080)
      curl -sS "http://127.0.0.1:${port}/health" || echo "curl failed (is subscription server running?)"
      ;;
    9) mask_env ;;
    10)
      if [[ -f .env ]]; then
        stat -c '%a %n' .env || ls -l .env
      fi
      grep -q '^\.env$' .gitignore 2>/dev/null && echo ".env is gitignored" || echo "WARN: add .env to .gitignore"
      ;;
    *) echo "Invalid" ;;
  esac
done
