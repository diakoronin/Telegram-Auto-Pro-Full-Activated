#!/usr/bin/env bash
# SakaBot manager — run: sakabot  (symlink to this file)
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/sakabot}"
SERVICE_NAME="${SERVICE_NAME:-sakabot}"
REPO_URL="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
INSTALL_BRANCH="${INSTALL_BRANCH:-main}"

require_root() {
  [[ "${EUID:-0}" -eq 0 ]] || { echo "با sudo اجرا کنید." >&2; exit 1; }
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
  [[ -f "${INSTALL_DIR}/.env" ]] || { echo "بدون .env"; return; }
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    mask_line "$line"
  done <"${INSTALL_DIR}/.env"
}

run_migrations() {
  bash "${INSTALL_DIR}/scripts/run_migrations.sh"
}

health() {
  curl -fsS "http://127.0.0.1:${SUBSCRIPTION_API_PORT:-8080}/health" && echo "" || echo "Health failed"
}

dup_check() {
  pgrep -af "${INSTALL_DIR}/.venv/bin/python.*${INSTALL_DIR}/main.py" || echo "پردازش تکراری یافت نشد"
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
  sudo -u postgres pg_dump sakabot >"$f" && echo "ذخیره شد: $f"
}

restore_db() {
  require_root
  read -r -p "مسیر فایل .sql: " fp
  [[ -f "$fp" ]] || { echo "فایل نیست"; return; }
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
  echo "1) نگه‌داشتن .env و DB  2) نصب کامل (سوالات دوباره)"
  read -r -p "انتخاب: " r
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
  pub=$(grep '^PUBLIC_BASE_URL=' "${INSTALL_DIR}/.env" | cut -d= -f2-)
  INSTALL_DIR="$INSTALL_DIR" bash -c 'source /dev/null' 2>/dev/null
  python3.11 - "$pub" <<'PY' 2>/dev/null || true
import os, sys, subprocess
# delegate to installer's nginx block: user runs certbot manually
print("از install.sh برای nginx کامل استفاده کنید یا فایل sites-available را ویرایش کنید.")
PY
  echo "نمونه: proxy /sub/ و /health به 127.0.0.1:8080 — در README و install.sh موجود است."
}

ssl_renew() {
  require_root
  certbot renew --nginx -n || certbot renew -n
}

send_backup_owner() {
  echo "بکاپ خودکار هنگام اجرای ربات با AUTO_BACKUP_ENABLED ارسال می‌شود؛ یا backup_db را اجرا کنید."
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
  echo "${key}=${val} (سرویس ری‌استارت شد)"
}

manual_import_help() {
  echo "در تلگرام: 📥 ایمپورت لینک TXT — خط اول: id سرور دستی,id پلن (مثال 1,1)"
}

manual_stock_sql() {
  echo "sudo -u postgres psql -d sakabot -c \"SELECT manual_server_id, manual_plan_id, status, count(*) FROM manual_links GROUP BY 1,2,3;\""
}

change_token() {
  require_root
  read -r -s -p "BOT_TOKEN جدید: " t
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
    echo "ابتدا install را از GitHub اجرا کنید (README)."
  fi
}

menu() {
  cat <<'M'
══ SakaBot Manager ══
 1) نصب اولیه (install.sh)
 2) به‌روزرسانی ربات
 3) نصب مجدد (منو)
 4) حذف نصب (install uninstall)
 5) شروع سرویس
 6) توقف سرویس
 7) ری‌استارت سرویس
 8) وضعیت سرویس
 9) لاگ زنده (journalctl)
10) ۱۰۰ خط آخر لاگ
11) راه‌اندازی PostgreSQL (راهنما)
12) بکاپ دیتابیس
13) بازیابی دیتابیس
14) تغییر BOT_TOKEN
15) تغییر OWNER_ID
16) تغییر PUBLIC_BASE_URL
17) نام پشتیبانی (SUPPORT_USERNAME)
18) نام برند (BRAND_NAME)
19) نمایش تنظیمات (ماسک)
20) نصب/تعمیر systemd (install.sh)
21) فعال‌سازی خودکار
22) غیرفعال‌سازی خودکار
23) Health check
24) پردازش‌های تکراری
25) حذف پردازش‌های تکراری
26) اجرای migration
27) بررسی chmod .env
28) فایروال UFW (راهنما)
29) htop
30) ارسال بکاپ به مالک (راهنما)
31) AUTO_BACKUP=true
32) AUTO_BACKUP=false
33) nginx (راهنما)
34) SSL renew
35) تست subscription curl
36) ایمپورت دستی (راهنما)
37) موجودی دستی SQL
38) MANUAL_MODE on/off
39) API_PRODUCTS on/off
 0) خروج
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
      11) echo "CREATE USER/DB — install.sh یا docs";;
      12) require_root; backup_db ;;
      13) restore_db ;;
      14) change_token ;;
      15) change_owner ;;
      16) change_public_url ;;
      17) read -r -p "SUPPORT_USERNAME: " s; require_root; sed -i "s/^SUPPORT_USERNAME=.*/SUPPORT_USERNAME=${s}/" "${INSTALL_DIR}/.env" 2>/dev/null || echo "SUPPORT_USERNAME=${s}" >>"${INSTALL_DIR}/.env"; systemctl restart "$SERVICE_NAME" ;;
      18) read -r -p "BRAND_NAME: " b; require_root; sed -i "s/^BRAND_NAME=.*/BRAND_NAME=${b}/" "${INSTALL_DIR}/.env"; systemctl restart "$SERVICE_NAME" ;;
      19) view_settings ;;
      20) require_root; [[ -f "${INSTALL_DIR}/install.sh" ]] && bash "${INSTALL_DIR}/install.sh" --install || echo "install.sh را از repo کپی کنید" ;;
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
      *) echo "نامعتبر" ;;
    esac
  done
}

# اگر مستقیماً با آرگومان فراخوانی شد (برای اسکریپت)
case "${1:-}" in
  --update) require_root; update_bot ;;
  "") main_loop ;;
  *) echo "استفاده: sakabot   یا   sudo sakabot" ;;
esac
