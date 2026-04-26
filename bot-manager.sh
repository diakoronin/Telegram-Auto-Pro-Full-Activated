#!/usr/bin/env bash
# Saka Bot Manager — Ubuntu 22.04/24.04. Run from repo root: sudo ./bot-manager.sh
set -euo pipefail

ROOT="${SAKA_BOT_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
cd "$ROOT"
UNIT="${SYSTEMD_UNIT:-telegram-sales-bot.service}"
VENV_PY="${ROOT}/.venv/bin/python"
PIP="${ROOT}/.venv/bin/pip"
SERVICE_FILE="/etc/systemd/system/${UNIT}"
BACKUP_DIR="${ROOT}/backups"

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[bot-manager] $*"; }

need_root() { [[ "$(id -u)" -eq 0 ]] || die "This action requires root (sudo)."; }

ensure_venv() {
  [[ -d "${ROOT}/.venv" ]] || python3 -m venv "${ROOT}/.venv"
  "$PIP" install -q -U pip
  "$PIP" install -q -r "${ROOT}/requirements.txt"
}

mask_env() {
  sed -E 's/^(BOT_TOKEN=).*/\1***/; s/(DATABASE_URL=.*:)([^@]+)(@)/\1***\3/; s/^(OWNER_ID=).*/\1***/; s/^(PANEL_CREDENTIAL_ENCRYPTION_KEY=).*/\1***/' "${ROOT}/.env" 2>/dev/null || true
}

set_env_kv() {
  local key="$1" val="$2"
  need_root
  [[ -f "${ROOT}/.env" ]] || die "Missing .env"
  if grep -q "^${key}=" "${ROOT}/.env"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${ROOT}/.env"
  else
    echo "${key}=${val}" >> "${ROOT}/.env"
  fi
  chmod 600 "${ROOT}/.env"
  log "Updated ${key}"
}

interactive_env() {
  need_root
  read -r -p "BOT_TOKEN: " bt || true
  read -r -p "OWNER_ID (numeric Telegram): " oid || true
  read -r -p "PUBLIC_BASE_URL (https://domain, no trailing slash): " pub || true
  read -r -p "SUPPORT_USERNAME (no @): " sup || true
  read -r -p "DATABASE_URL (async URL): " dbu || true
  cat > "${ROOT}/.env" <<EOF
BOT_TOKEN=${bt}
OWNER_ID=${oid}
DATABASE_URL=${dbu}
SUPPORT_USERNAME=${sup}
PUBLIC_BASE_URL=${pub}
BRAND_NAME=ساکانت
TIMEZONE=Asia/Tehran
EOF
  chmod 600 "${ROOT}/.env"
  mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR" || true
  log ".env written"
}

install_postgresql_local() {
  need_root
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib
  systemctl enable --now postgresql
  read -r -p "DB user name [saka_bot]: " dbuser || true
  dbuser=${dbuser:-saka_bot}
  read -r -p "DB name [saka_bot]: " dbname || true
  dbname=${dbname:-saka_bot}
  read -r -s -p "DB password: " dbpass || true
  echo
  sudo -u postgres psql -v ON_ERROR_STOP=1 <<PSQL
CREATE USER ${dbuser} WITH PASSWORD '${dbpass}';
CREATE DATABASE ${dbname} OWNER ${dbuser};
PSQL
  echo "DATABASE_URL=postgresql+asyncpg://${dbuser}:${dbpass}@127.0.0.1:5432/${dbname}" >> "${ROOT}/.env"
  chmod 600 "${ROOT}/.env"
  log "PostgreSQL user/db created; DATABASE_URL appended to .env"
}

write_systemd_unit() {
  need_root
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Telegram VPN sales bot
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=${ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${VENV_PY} ${ROOT}/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  log "systemd unit $SERVICE_FILE written"
}

dup_bot_pids() {
  pgrep -af "python.*main.py" 2>/dev/null || true
}

menu() {
  echo ""
  echo "========== Saka Bot Manager (0–35) =========="
  echo "0) Exit"
  echo "1) Install bot (deps + venv)"
  echo "2) Update bot (git pull + pip)"
  echo "3) Reinstall bot (submenu)"
  echo "4) Uninstall bot (submenu)"
  echo "5) Start bot"
  echo "6) Stop bot"
  echo "7) Restart bot"
  echo "8) Status"
  echo "9) Live logs (follow)"
  echo "10) Last 100 logs"
  echo "11) Setup PostgreSQL (local apt + create db user)"
  echo "12) Backup database (local zip in backups/)"
  echo "13) Restore database (manual: unpack + point DATABASE_URL)"
  echo "14) Change BOT_TOKEN"
  echo "15) Change OWNER_ID"
  echo "16) Change PUBLIC_BASE_URL"
  echo "17) Change SUPPORT_USERNAME"
  echo "18) Change BRAND_NAME"
  echo "19) View current settings (masked)"
  echo "20) Install/repair systemd service"
  echo "21) Enable autostart (systemctl enable)"
  echo "22) Disable autostart (systemctl disable)"
  echo "23) Check health (curl /health)"
  echo "24) Check duplicate processes"
  echo "25) Kill duplicate processes (SIGTERM all main.py)"
  echo "26) Run migrations (check_startup)"
  echo "27) Security check (.env chmod, backups dir)"
  echo "28) Firewall setup (ufw allow 22,80,443 + sub port)"
  echo "29) Install monitoring tools (htop iotop)"
  echo "30) Send backup to owner now (run bot Python one-shot — needs .env)"
  echo "31) Enable hourly backups (AUTO_BACKUP_ENABLED=true)"
  echo "32) Disable hourly backups"
  echo "33) Setup nginx subscription endpoint (snippet file)"
  echo "34) Renew SSL (certbot renew --dry-run or live)"
  echo "35) Test subscription endpoint (curl /health)"
  echo "=============================================="
}

reinstall_sub() {
  echo "1) Keep DB and .env — only refresh code and pip"
  echo "2) Keep .env — reset DB URL (you edit DATABASE_URL manually after)"
  echo "3) Full: remove .venv and reinstall pip"
  read -r -p "Choice: " c || true
  case "$c" in
    1) git pull --ff-only || true; ensure_venv ;;
    2) : > "${ROOT}/.env.db_reset_note"; log "Create new DB and update .env manually" ;;
    3) rm -rf "${ROOT}/.venv"; ensure_venv ;;
    *) echo "Invalid" ;;
  esac
}

uninstall_sub() {
  echo "1) Remove venv only"
  echo "2) Remove systemd service only"
  echo "3) Stop service, keep files and DB"
  echo "4) Full delete: stop service, remove repo (DANGER)"
  read -r -p "Choice: " c || true
  case "$c" in
    1) rm -rf "${ROOT}/.venv" ;;
    2) need_root; systemctl disable --now "$UNIT" 2>/dev/null || true; rm -f "$SERVICE_FILE"; systemctl daemon-reload ;;
    3) need_root; systemctl disable --now "$UNIT" 2>/dev/null || true ;;
    4) need_root; systemctl disable --now "$UNIT" 2>/dev/null || true; rm -rf "$ROOT"; die "Removed $ROOT — exited" ;;
    *) echo "Invalid" ;;
  esac
}

nginx_snippet() {
  need_root
  local port
  port=$(grep SUBSCRIPTION_BIND_PORT "${ROOT}/.env" 2>/dev/null | cut -d= -f2 || echo 8080)
  local out="/etc/nginx/snippets/saka-subscription.conf"
  mkdir -p "$(dirname "$out")"
  cat > "$out" <<NGX
# Include in server { } block; set server_name and SSL paths.
location /sub/ {
    proxy_pass http://127.0.0.1:${port};
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
}
NGX
  log "Wrote $out — nginx -t && systemctl reload nginx"
}

while true; do
  menu
  read -r -p "Choice [0-35]: " choice || exit 0
  case "$choice" in
    0) exit 0 ;;
    1)
      [[ -d .git ]] || die "Not a git repo"
      git pull --ff-only || true
      ensure_venv
      need_root
      mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR" || true
      [[ -f .env ]] && chmod 600 .env || log "Create .env (menu 99 interactive or copy .env.example)"
      log "install done"
      ;;
    2)
      git pull --ff-only || true
      ensure_venv
      ;;
    3) reinstall_sub ;;
    4) uninstall_sub ;;
    5) need_root; systemctl start "$UNIT" || die "start failed" ;;
    6) need_root; systemctl stop "$UNIT" || true ;;
    7) need_root; systemctl restart "$UNIT" || die "restart failed" ;;
    8) need_root; systemctl status "$UNIT" --no-pager || true ;;
    9) need_root; journalctl -u "$UNIT" -f ;;
    10) need_root; journalctl -u "$UNIT" -n 100 --no-pager || true ;;
    11) install_postgresql_local ;;
    12)
      need_root
      mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
      ensure_venv
      "$VENV_PY" "${ROOT}/scripts/manager_local_backup.py" || log "local backup failed"
      ;;
    13) log "Restore: unzip backup, for SQLite point DATABASE_URL to file; for PG use pg_restore." ;;
    14) read -r -p "BOT_TOKEN: " v; set_env_kv BOT_TOKEN "$v" ;;
    15) read -r -p "OWNER_ID: " v; set_env_kv OWNER_ID "$v" ;;
    16) read -r -p "PUBLIC_BASE_URL: " v; set_env_kv PUBLIC_BASE_URL "$v" ;;
    17) read -r -p "SUPPORT_USERNAME: " v; set_env_kv SUPPORT_USERNAME "$v" ;;
    18) read -r -p "BRAND_NAME: " v; set_env_kv BRAND_NAME "$v" ;;
    19) mask_env ;;
    20) ensure_venv; write_systemd_unit ;;
    21) need_root; systemctl enable "$UNIT" ;;
    22) need_root; systemctl disable "$UNIT" || true ;;
    23)
      port=$(grep SUBSCRIPTION_BIND_PORT .env 2>/dev/null | cut -d= -f2 || echo 8080)
      curl -sS "http://127.0.0.1:${port}/health" || echo "curl failed"
      ;;
    24) dup_bot_pids ;;
    25) need_root; pkill -f "${ROOT}/main.py" || true; log "Sent SIGTERM to matching processes" ;;
    26) [[ -f .env ]] || die "Missing .env"; ensure_venv; "$VENV_PY" "${ROOT}/scripts/check_startup.py" || true ;;
    27)
      need_root
      [[ -f .env ]] && chmod 600 .env || true
      mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR" || true
      grep -q '^\.env$' .gitignore && echo ".env gitignored OK" || echo "WARN: .env not in .gitignore"
      ;;
    28)
      need_root
      apt-get install -y -qq ufw || true
      ufw allow 22/tcp || true
      ufw allow 80/tcp || true
      ufw allow 443/tcp || true
      sp=$(grep SUBSCRIPTION_BIND_PORT .env 2>/dev/null | cut -d= -f2 || echo 8080)
      ufw allow "${sp}/tcp" || true
      echo "Run 'ufw enable' manually if not already enabled."
      ;;
    29) need_root; apt-get install -y -qq htop iotop || true ;;
    30)
      [[ -f .env ]] || die "Missing .env"
      ensure_venv
      "$VENV_PY" "${ROOT}/scripts/manager_send_owner_backup.py" || true
      ;;
    31) set_env_kv AUTO_BACKUP_ENABLED true ;;
    32) set_env_kv AUTO_BACKUP_ENABLED false ;;
    33) nginx_snippet ;;
    34)
      need_root
      if command -v certbot >/dev/null; then certbot renew --dry-run || certbot renew; else apt-get install -y -qq certbot; echo "Install certbot plugin for nginx and run certbot --nginx"; fi
      ;;
    35)
      port=$(grep SUBSCRIPTION_BIND_PORT .env 2>/dev/null | cut -d= -f2 || echo 8080)
      curl -sS -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:${port}/health"
      ;;
    99) interactive_env ;; # hidden helper
    *) echo "Invalid choice" ;;
  esac
done
