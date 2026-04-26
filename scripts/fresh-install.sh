#!/usr/bin/env bash
# Full wipe + latest install (Debian/Ubuntu, run as root). Use SSH with TTY for BOT_TOKEN/OWNER_ID.
#
#   curl -fsSL "https://raw.githubusercontent.com/ORG/REPO/BRANCH/scripts/fresh-install.sh" | sudo bash -s -- /root/telegram-sales-bot BRANCH
#
# Env:
#   REPO_URL          — GitHub HTTPS repo (for raw install.sh URL)
#   FRESH_DROP_DB=1   — before rm: drop Postgres DB+role from old .env (optional)
#   SKIP_SYSTEMD=1    — skip install-systemd.sh at end
#   SAKA_BOT_UNIT     — systemd unit name (default telegram-sales-bot.service)

set -euo pipefail

INSTALL_DIR="${1:-${INSTALL_DIR:-/root/telegram-sales-bot}}"
BRANCH="${2:-${BRANCH:-main}}"
REPO_URL="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
UNIT="${SAKA_BOT_UNIT:-${SYSTEMD_UNIT:-telegram-sales-bot.service}}"

log() { printf '%s\n' "[fresh-install] $*"; }
die() { printf '%s\n' "[fresh-install] ERROR: $*" >&2; exit 1; }

if [[ "$(id -u)" -ne 0 ]]; then
  die "Run as root: curl ... | sudo bash -s -- /root/telegram-sales-bot BRANCH"
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

repo_to_raw_install_url() {
  local url="$1" br="$2"
  local r="${url#https://github.com/}"
  r="${r#http://github.com/}"
  r="${r%.git}"
  [[ -n "$r" ]] || die "Bad REPO_URL: $url"
  printf 'https://raw.githubusercontent.com/%s/%s/scripts/install.sh' "$r" "$br"
}

stop_all() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop "$UNIT" 2>/dev/null || true
    systemctl disable "$UNIT" 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
  fi
  if [[ -d "$INSTALL_DIR" ]]; then
    pkill -f "${INSTALL_DIR}/main.py" 2>/dev/null || true
  fi
  pkill -f "telegram-sales-bot.*main.py" 2>/dev/null || true
  sleep 2
}

maybe_drop_old_database() {
  [[ "${FRESH_DROP_DB:-0}" == "1" ]] || return 0
  [[ -f "$INSTALL_DIR/.env" ]] || return 0
  command -v dropdb >/dev/null 2>&1 || return 0
  log "FRESH_DROP_DB=1: dropping old Postgres DB from .env ..."
  export FRESH_ENV_FILE="$INSTALL_DIR/.env"
  python3 <<'PY' || true
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

p = Path(os.environ.get("FRESH_ENV_FILE", ""))
if not p.is_file():
    sys.exit(0)
data = {}
for line in p.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    data[k.strip()] = v.strip()
url = data.get("DATABASE_URL", "")
if not url.startswith("postgresql"):
    sys.exit(0)
u = urlparse(url.replace("postgresql+asyncpg", "postgresql", 1))
db = (u.path or "/").lstrip("/")
user = unquote(u.username or "")
if not db:
    sys.exit(0)
safe_db = db.replace("'", "''")
subprocess.run(
    [
        "sudo",
        "-u",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=0",
        "-c",
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{safe_db}' AND pid <> pg_backend_pid();",
    ],
    capture_output=True,
)
subprocess.run(
    ["sudo", "-u", "postgres", "dropdb", "--if-exists", db],
    capture_output=True,
)
if user:
    safe_u = user.replace('"', "")
    subprocess.run(
        [
            "sudo",
            "-u",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=0",
            "-c",
            f'DROP ROLE IF EXISTS "{safe_u}";',
        ],
        capture_output=True,
    )
print("dropdb/droprole done (ignored errors if missing)")
PY
}

remove_unit_file() {
  local f="/etc/systemd/system/$UNIT"
  if [[ -f "$f" ]]; then
    rm -f "$f"
    systemctl daemon-reload || true
    log "Removed $f"
  fi
}

main() {
  need_cmd curl
  log "INSTALL_DIR=$INSTALL_DIR BRANCH=$BRANCH UNIT=$UNIT"
  stop_all
  maybe_drop_old_database
  if [[ -e "$INSTALL_DIR" ]]; then
    log "Removing $INSTALL_DIR ..."
    rm -rf "$INSTALL_DIR"
  fi
  remove_unit_file

  INSTALL_URL="$(repo_to_raw_install_url "$REPO_URL" "$BRANCH")"
  log "Running install from: $INSTALL_URL"
  curl -fsSL "$INSTALL_URL" | bash -s -- "$INSTALL_DIR" "$BRANCH"

  if [[ "${SKIP_SYSTEMD:-}" != "1" ]] && [[ -f "$INSTALL_DIR/scripts/install-systemd.sh" ]]; then
    log "Installing systemd unit..."
    bash "$INSTALL_DIR/scripts/install-systemd.sh"
  fi

  if [[ -f "$INSTALL_DIR/scripts/saka-bot" ]]; then
    chmod +x "$INSTALL_DIR/scripts/saka-bot"
    ln -sf "$INSTALL_DIR/scripts/saka-bot" /usr/local/bin/saka-bot 2>/dev/null || true
    log "Linked: /usr/local/bin/saka-bot"
  fi

  log "Done. systemctl status $UNIT"
  log "Update later: cd $INSTALL_DIR && saka-bot update"
}

main "$@"
