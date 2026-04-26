#!/usr/bin/env bash
# SakaBot manager — run: sakabot  (symlink to this file)
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sakabot}"
SERVICE_NAME="${SERVICE_NAME:-sakabot}"
REPO_URL="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
INSTALL_BRANCH="${INSTALL_BRANCH:-main}"

require_root() {
  [[ "${EUID:-0}" -eq 0 ]] || { echo "Run as root: sudo sakabot" >&2; exit 1; }
}

mask_line() {
  local line="$1"
  local k="${line%%=*}"
  case "$k" in
    BOT_TOKEN|PANEL_CREDENTIAL_ENCRYPTION_KEY|DATABASE_URL) echo "$k=***" ;;
    *) echo "$line" ;;
  esac
}

view_settings() {
  [[ -f "${INSTALL_DIR}/.env" ]] || { echo "No .env file"; return; }
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    mask_line "$line"
  done <"${INSTALL_DIR}/.env"
}

run_migrations() {
  bash "${INSTALL_DIR}/scripts/run_migrations.sh"
}

health() {
  curl -fsS "http://127.0.0.1:${SUBSCRIPTION_API_PORT:-8080}/health" && echo "" || echo "Health check failed"
}

dup_check() {
  pgrep -af "${INSTALL_DIR}/.venv/bin/python.*${INSTALL_DIR}/main.py" || echo "No duplicate process found"
}

dup_kill() {
  require_root
  pkill -f "${INSTALL_DIR}/.venv/bin/python.*${INSTALL_DIR}/main.py" 2>/dev/null || true
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
}

backup_db() {
  require_root
  mkdir -p "${INSTALL_DIR}/backups"
  local f="${INSTALL_DIR}/backups/manual_$(date +%Y%m%d_%H%M%S).sql"
  sudo -u postgres pg_dump sakabot >"$f" && echo "Saved: $f"
}

restore_db() {
  require_root
  read -r -p "Path to .sql file: " fp
  [[ -f "$fp" ]] || { echo "File not found"; return; }
  sudo -u postgres psql -d sakabot -f "$fp"
}

update_bot() {
  require_root
  local inst_url="https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${INSTALL_BRANCH}/install.sh"
  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then
    bash "${INSTALL_DIR}/install.sh" --update
  else
    curl -fsSL "$inst_url" | bash -s -- --update
  fi
}

reinstall_menu() {
  require_root
  local inst_url="https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${INSTALL_BRANCH}/install.sh"
  echo "1) Reinstall, keep .env + DB  2) Full reinstall (prompts again)"
  read -r -p "Choice: " r
  case "$r" in
    1)
      if [[ -f "${INSTALL_DIR}/install.sh" ]]; then bash "${INSTALL_DIR}/install.sh" --reinstall
      else curl -fsSL "$inst_url" | bash -s -- --reinstall
      fi
      ;;
    2)
      if [[ -f "${INSTALL_DIR}/install.sh" ]]; then bash "${INSTALL_DIR}/install.sh" --reinstall-full
      else curl -fsSL "$inst_url" | bash -s -- --reinstall-full
      fi
      ;;
  esac
}

uninstall_bot() {
  local inst_url="https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${INSTALL_BRANCH}/install.sh"
  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then bash "${INSTALL_DIR}/install.sh" --uninstall
  else curl -fsSL "$inst_url" | bash -s -- --uninstall
  fi
}

nginx_setup() {
  require_root
  echo "Edit nginx site to proxy /sub/ and /health to 127.0.0.1:8080 (see README / install.sh)."
}

ssl_renew() {
  require_root
  certbot renew --nginx -n || certbot renew -n
}

send_backup_owner() {
  echo "Hourly backup runs when bot is up with AUTO_BACKUP_ENABLED=true; or use menu 12 for manual SQL dump."
}

toggle_env_key() {
  local key="$1" val="$2"
  require_root
  [[ -f "${INSTALL_DIR}/.env" ]] || return
  if grep -q "^${key}=" "${INSTALL_DIR}/.env"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${INSTALL_DIR}/.env"
  else
    echo "${key}=${val}" >>"${INSTALL_DIR}/.env"
  fi
  systemctl restart "$SERVICE_NAME" 2>/dev/null || true
  echo "${key}=${val} (service restarted if running)"
}

manual_import_help() {
  echo "In Telegram admin: manual TXT import — first line: manual_server_id,manual_plan_id (e.g. 1,1)"
}

manual_stock_sql() {
  echo "sudo -u postgres psql -d sakabot -c \"SELECT manual_server_id, manual_plan_id, status, count(*) FROM manual_links GROUP BY 1,2,3;\""
}

change_token() {
  require_root
  read -r -s -p "New BOT_TOKEN: " t
  echo
  python3 - "$INSTALL_DIR/.env" "$t" <<'PY'
import sys
path, token = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
out = []
seen = False
for line in lines:
    if line.startswith("BOT_TOKEN="):
        out.append("BOT_TOKEN=" + token)
        seen = True
    else:
        out.append(line)
if not seen:
    out.insert(0, "BOT_TOKEN=" + token)
open(path, "w").write("\n".join(out) + "\n")
PY
  chmod 600 "${INSTALL_DIR}/.env"
  curl -fsS "https://api.telegram.org/bot${t}/deleteWebhook?drop_pending_updates=true" >/dev/null 2>&1 || true
  systemctl restart "$SERVICE_NAME"
}

change_owner() {
  require_root
  read -r -p "OWNER_ID: " oid
  sed -i "s/^OWNER_ID=.*/OWNER_ID=${oid}/" "${INSTALL_DIR}/.env"
  systemctl restart "$SERVICE_NAME"
}

change_public_url() {
  require_root
  read -r -p "PUBLIC_BASE_URL: " u
  sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${u}|" "${INSTALL_DIR}/.env"
  systemctl restart "$SERVICE_NAME"
}

install_first_time() {
  require_root
  if [[ -f "${INSTALL_DIR}/install.sh" ]]; then
    bash "${INSTALL_DIR}/install.sh" --install
  else
    echo "Run installer from GitHub first (see README)."
  fi
}

menu() {
  cat <<'M'
=== SakaBot Manager ===
 1) First-time install (install.sh)
 2) Update bot
 3) Reinstall (submenu)
 4) Uninstall (install.sh --uninstall)
 5) Start service
 6) Stop service
 7) Restart service
 8) Service status
 9) Follow logs (journalctl)
10) Last 100 log lines
11) PostgreSQL setup (hint)
12) Backup database (SQL)
13) Restore database
14) Change BOT_TOKEN
15) Change OWNER_ID
16) Change PUBLIC_BASE_URL
17) SUPPORT_USERNAME
18) BRAND_NAME (use Persian in .env for Telegram if you want)
19) Show settings (masked)
20) Repair systemd (run install.sh)
21) Enable service on boot
22) Disable service on boot
23) Health check
24) List duplicate processes
25) Kill duplicate processes + stop service
26) Run migrations
27) chmod 600 .env
28) UFW hint
29) htop
30) Backup to owner (hint)
31) AUTO_BACKUP_ENABLED=true
32) AUTO_BACKUP_ENABLED=false
33) nginx hint
34) SSL renew (certbot)
35) Test subscription endpoint (curl)
36) Manual import (hint)
37) Manual stock SQL
38) MANUAL_MODE_ENABLED true/false
39) API_PRODUCTS_ENABLED true/false
 0) Exit
M
}

main_loop() {
  while true; do
    menu
    read -r -p "> " choice || exit 0
    case "$choice" in
      0) exit 0 ;;
      1) install_first_time ;;
      2) update_bot ;;
      3) reinstall_menu ;;
      4) require_root; uninstall_bot ;;
      5) require_root; systemctl start "$SERVICE_NAME" ;;
      6) require_root; systemctl stop "$SERVICE_NAME" ;;
      7) require_root; systemctl restart "$SERVICE_NAME" ;;
      8) systemctl status "$SERVICE_NAME" --no-pager || true ;;
      9) journalctl -u "$SERVICE_NAME" -f ;;
      10) journalctl -u "$SERVICE_NAME" -n 100 --no-pager ;;
      11) echo "Hint: use install.sh for DB user sakabot, or: sudo -u postgres createuser/createdb";;
      12) require_root; backup_db ;;
      13) restore_db ;;
      14) change_token ;;
      15) change_owner ;;
      16) change_public_url ;;
      17) read -r -p "SUPPORT_USERNAME: " s; require_root; sed -i "s/^SUPPORT_USERNAME=.*/SUPPORT_USERNAME=${s}/" "${INSTALL_DIR}/.env" 2>/dev/null || echo "SUPPORT_USERNAME=${s}" >>"${INSTALL_DIR}/.env"; systemctl restart "$SERVICE_NAME" ;;
      18) read -r -p "BRAND_NAME: " b; require_root; sed -i "s/^BRAND_NAME=.*/BRAND_NAME=${b}/" "${INSTALL_DIR}/.env"; systemctl restart "$SERVICE_NAME" ;;
      19) view_settings ;;
      20) require_root; [[ -f "${INSTALL_DIR}/install.sh" ]] && bash "${INSTALL_DIR}/install.sh" --install || echo "Copy install.sh from repo into ${INSTALL_DIR}" ;;
      21) require_root; systemctl enable "$SERVICE_NAME" ;;
      22) require_root; systemctl disable "$SERVICE_NAME" ;;
      23) health ;;
      24) dup_check ;;
      25) dup_kill ;;
      26) require_root; run_migrations; systemctl restart "$SERVICE_NAME" ;;
      27) require_root; chmod 600 "${INSTALL_DIR}/.env"; ls -la "${INSTALL_DIR}/.env" ;;
      28) echo "sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw enable" ;;
      29) htop || true ;;
      30) send_backup_owner ;;
      31) require_root; toggle_env_key "AUTO_BACKUP_ENABLED" "true" ;;
      32) require_root; toggle_env_key "AUTO_BACKUP_ENABLED" "false" ;;
      33) nginx_setup ;;
      34) ssl_renew ;;
      35) health; curl -fsSI "http://127.0.0.1:8080/health" | head -5 ;;
      36) manual_import_help ;;
      37) manual_stock_sql ;;
      38) read -r -p "true/false: " v; require_root; toggle_env_key "MANUAL_MODE_ENABLED" "$v" ;;
      39) read -r -p "true/false: " v; require_root; toggle_env_key "API_PRODUCTS_ENABLED" "$v" ;;
      *) echo "Invalid choice" ;;
    esac
  done
}

case "${1:-}" in
  --update) require_root; update_bot ;;
  "") main_loop ;;
  *) echo "Usage: sakabot   or   sudo sakabot" ;;
esac
