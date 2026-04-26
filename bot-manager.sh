#!/usr/bin/env bash
# VPS manager for Telegram VPN sales bot (Ubuntu 22.04/24.04)
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sakabot}"
VENV="${INSTALL_DIR}/venv"
SERVICE_NAME="${SERVICE_NAME:-telegram-vpn-bot}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "این گزینه نیاز به اجرای با sudo دارد." >&2
    exit 1
  fi
}

mask_env_value() {
  local k="$1" v="$2"
  case "$k" in
    BOT_TOKEN|PANEL_CREDENTIAL_ENCRYPTION_KEY|DATABASE_URL) echo "***" ;;
    *) echo "$v" ;;
  esac
}

show_settings() {
  if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    echo "فایل .env یافت نشد."
    return
  fi
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    k="${line%%=*}"
    v="${line#*=}"
    echo "$k=$(mask_env_value "$k" "$v")"
  done <"${INSTALL_DIR}/.env"
}

install_bot() {
  require_root
  apt-get update -y
  apt-get install -y python3.11 python3.11-venv python3-pip postgresql-client curl ufw nginx certbot python3-certbot-nginx || true
  id -u sakabot &>/dev/null || useradd -r -m -d "$INSTALL_DIR" -s /bin/bash sakabot
  mkdir -p "$INSTALL_DIR" backups logs
  rsync -a --delete "${REPO_ROOT}/" "${INSTALL_DIR}/app_src/" || cp -a "${REPO_ROOT}/." "${INSTALL_DIR}/app_src/"
  python3.11 -m venv "$VENV"
  "$VENV/bin/pip" install -U pip
  "$VENV/bin/pip" install -r "${INSTALL_DIR}/app_src/requirements.txt"
  ln -sf "${INSTALL_DIR}/app_src/main.py" "${INSTALL_DIR}/main.py"
  ln -sf "${INSTALL_DIR}/app_src/bot_app" "${INSTALL_DIR}/bot_app"
  chown -R sakabot:sakabot "$INSTALL_DIR" backups logs
  chmod 700 "$INSTALL_DIR" backups || true
  if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    read -rp "BOT_TOKEN: " bt
    read -rp "OWNER_ID (numeric): " oid
    read -rp "PUBLIC_BASE_URL: " pub
    read -rp "DATABASE_URL: " dbu
    read -rp "PANEL_CREDENTIAL_ENCRYPTION_KEY (min 16 chars): " penc
    cat >"${INSTALL_DIR}/.env" <<EOF
BOT_TOKEN=${bt}
OWNER_ID=${oid}
DATABASE_URL=${dbu}
PUBLIC_BASE_URL=${pub}
PANEL_CREDENTIAL_ENCRYPTION_KEY=${penc}
EOF
    chmod 600 "${INSTALL_DIR}/.env"
  fi
  echo "نصب اولیه انجام شد. گزینه ۲۰ را برای سرویس systemd اجرا کنید."
}

run_migrations() {
  if [[ ! -d "$VENV" ]]; then
    echo "venv یافت نشد. ابتدا نصب را انجام دهید."
    exit 1
  fi
  set -a
  # shellcheck source=/dev/null
  source "${INSTALL_DIR}/.env"
  set +a
  "$VENV/bin/python3" -c "
import asyncio
from bot_app.config import get_settings
from bot_app.db.session import get_engine, reset_engine
from bot_app.migrations.runner import run_migrations
async def main():
    reset_engine()
    s = get_settings()
    eng = get_engine(s.database_url)
    await run_migrations(eng)
    await eng.dispose()
asyncio.run(main())
"
}

health_check() {
  curl -fsS "http://127.0.0.1:${SUBSCRIPTION_API_PORT:-8080}/health" && echo " OK" || echo "subscription API fail"
}

duplicate_procs() {
  pgrep -af "python.*main.py" || true
}

kill_dupes() {
  require_root
  pkill -f "python.*${INSTALL_DIR}/main.py" || true
}

systemd_install() {
  require_root
  cp "${REPO_ROOT}/deploy/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
  sed -i "s|/opt/sakabot|${INSTALL_DIR}|g" "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  echo "سرویس ${SERVICE_NAME} ثبت شد."
}

menu() {
  cat <<'MENU'
0) خروج
1) نصب ربات
2) به‌روزرسانی ربات
3) نصب مجدد
4) حذف نصب
5) شروع
6) توقف
7) ری‌استارت
8) وضعیت
9) لاگ زنده
10) ۱۰۰ لاگ آخر
11) راه‌اندازی PostgreSQL (راهنما)
12) بکاپ دیتابیس
13) بازیابی دیتابیس
14) تغییر BOT_TOKEN
15) تغییر OWNER_ID
16) تغییر PUBLIC_BASE_URL
17) تغییر نام پشتیبانی
18) تغییر نام برند
19) نمایش تنظیمات (ماسک‌شده)
20) نصب/تعمیر systemd
21) فعال‌سازی خودکار اجرا
22) غیرفعال‌سازی خودکار اجرا
23) بررسی سلامت
24) بررسی پردازش‌های تکراری
25) حذف پردازش‌های تکراری
26) اجرای migration
27) بررسی امنیتی سریع
28) فایروال پایه
29) ابزار مانیتورینگ (htop)
30) ارسال بکاپ به مالک (از داخل ربات / bot process)
31) فعال‌سازی بکاپ ساعتی
32) غیرفعال‌سازی بکاپ ساعتی
33) نمونه nginx برای سابسکریپشن
34) تمدید SSL (certbot)
35) تست اندپوینت سابسکریپشن
36) ایمپورت لینک دستی از TXT (راهنما)
37) نمایش موجودی دستی (SQL)
38) روشن/خاموش manual mode در .env
39) روشن/خاموش API products در .env
MENU
}

while true; do
  menu
  read -rp "انتخاب: " choice
  case "$choice" in
    0) exit 0 ;;
    1) install_bot ;;
    2)
      require_root
      rsync -a "${REPO_ROOT}/" "${INSTALL_DIR}/app_src/"
      "$VENV/bin/pip" install -r "${INSTALL_DIR}/app_src/requirements.txt"
      systemctl restart "${SERVICE_NAME}" 2>/dev/null || true
      ;;
    3) install_bot ;;
    4)
      require_root
      systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
      rm -rf "$INSTALL_DIR"
      ;;
    5) require_root; systemctl start "${SERVICE_NAME}" ;;
    6) require_root; systemctl stop "${SERVICE_NAME}" ;;
    7) require_root; systemctl restart "${SERVICE_NAME}" ;;
    8) systemctl status "${SERVICE_NAME}" --no-pager || true ;;
    9) journalctl -u "${SERVICE_NAME}" -f ;;
    10) journalctl -u "${SERVICE_NAME}" -n 100 --no-pager ;;
    11)
      echo "sudo -u postgres psql -c \"CREATE USER sakabot WITH PASSWORD '...';\""
      echo "sudo -u postgres psql -c \"CREATE DATABASE sakabot OWNER sakabot;\""
      ;;
    12) require_root; run_migrations; pg_dump "$(grep DATABASE_URL "${INSTALL_DIR}/.env" | cut -d= -f2-)" >"${INSTALL_DIR}/backup_manual.sql" ;;
    13) echo "بازیابی: psql DATABASE_URL < backup.sql";;
    14) read -rp "BOT_TOKEN: " v; sed -i "s/^BOT_TOKEN=.*/BOT_TOKEN=${v}/" "${INSTALL_DIR}/.env"; chmod 600 "${INSTALL_DIR}/.env" ;;
    15) read -rp "OWNER_ID: " v; sed -i "s/^OWNER_ID=.*/OWNER_ID=${v}/" "${INSTALL_DIR}/.env" ;;
    16) read -rp "PUBLIC_BASE_URL: " v; sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=${v}|" "${INSTALL_DIR}/.env" ;;
    17) read -rp "SUPPORT_USERNAME: " v; grep -q SUPPORT_USERNAME "${INSTALL_DIR}/.env" && sed -i "s/^SUPPORT_USERNAME=.*/SUPPORT_USERNAME=${v}/" "${INSTALL_DIR}/.env" || echo "SUPPORT_USERNAME=${v}" >>"${INSTALL_DIR}/.env" ;;
    18) read -rp "BRAND_NAME: " v; sed -i "s/^BRAND_NAME=.*/BRAND_NAME=${v}/" "${INSTALL_DIR}/.env" ;;
    19) show_settings ;;
    20) systemd_install ;;
    21) require_root; systemctl enable "${SERVICE_NAME}" ;;
    22) require_root; systemctl disable "${SERVICE_NAME}" ;;
    23) health_check ;;
    24) duplicate_procs ;;
    25) kill_dupes ;;
    26) run_migrations ;;
    27)
      [[ -f "${INSTALL_DIR}/.env" ]] && stat -c '%a %n' "${INSTALL_DIR}/.env"
      ;;
    28) require_root; ufw allow OpenSSH; ufw allow 80,443/tcp; ufw --force enable ;;
    29) apt-get install -y htop ;;
    30) echo "از منوی ادمین ربات یا اجرای ربات با AUTO_BACKUP_ENABLED استفاده کنید." ;;
    31) sed -i 's/^AUTO_BACKUP_ENABLED=.*/AUTO_BACKUP_ENABLED=true/' "${INSTALL_DIR}/.env" 2>/dev/null || echo "AUTO_BACKUP_ENABLED=true" >>"${INSTALL_DIR}/.env" ;;
    32) sed -i 's/^AUTO_BACKUP_ENABLED=.*/AUTO_BACKUP_ENABLED=false/' "${INSTALL_DIR}/.env" ;;
    33)
      cat <<'NGX'
server {
  listen 443 ssl;
  server_name your-domain.com;
  location /sub/ {
    proxy_pass http://127.0.0.1:8080/sub/;
    proxy_set_header Host $host;
  }
}
NGX
      ;;
    34) certbot renew --nginx -n ;;
    35) health_check ;;
    36) echo "از پنل ادمین ربات: 📥 ایمپورت لینک TXT";;
    37) echo "SELECT manual_server_id, manual_plan_id, status, count(*) FROM manual_links GROUP BY 1,2,3;";;
    38)
      read -rp "MANUAL_MODE_ENABLED true/false: " v
      grep -q MANUAL_MODE_ENABLED "${INSTALL_DIR}/.env" && sed -i "s/^MANUAL_MODE_ENABLED=.*/MANUAL_MODE_ENABLED=${v}/" "${INSTALL_DIR}/.env" || echo "MANUAL_MODE_ENABLED=${v}" >>"${INSTALL_DIR}/.env"
      ;;
    39)
      read -rp "API_PRODUCTS_ENABLED true/false: " v
      grep -q API_PRODUCTS_ENABLED "${INSTALL_DIR}/.env" && sed -i "s/^API_PRODUCTS_ENABLED=.*/API_PRODUCTS_ENABLED=${v}/" "${INSTALL_DIR}/.env" || echo "API_PRODUCTS_ENABLED=${v}" >>"${INSTALL_DIR}/.env"
      ;;
    *) echo "گزینه نامعتبر" ;;
  esac
done
