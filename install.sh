#!/usr/bin/env bash
# SakaBot — one-line installer (Ubuntu 22.04 / 24.04)
# bash <(curl -Ls https://raw.githubusercontent.com/USER/REPO/main/install.sh)
# INSTALL_BRANCH=main INSTALL_DIR=/opt/sakabot bash <(curl -Ls ...)

set -Eeuo pipefail

INSTALL_LOG="${INSTALL_LOG:-/tmp/sakabot-install.log}"
REPO_URL="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
DEFAULT_DIR="/opt/sakabot"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
INSTALL_BRANCH="${INSTALL_BRANCH:-main}"
INSTALL_DEV="${INSTALL_DEV:-false}"

G="\033[0;32m"; Y="\033[1;33m"; R="\033[0;31m"; C="\033[0;36m"; N="\033[0m"

log() { echo -e "${C}[sakabot]${N} $*" | tee -a "$INSTALL_LOG" >&2; }
ok() { echo -e "${G}✓${N} $*" | tee -a "$INSTALL_LOG" >&2; }
warn() { echo -e "${Y}!${N} $*" | tee -a "$INSTALL_LOG" >&2; }
die() { echo -e "${R}✗${N} $*" | tee -a "$INSTALL_LOG" >&2; exit 1; }

trap 'ec=$?; die "خطا در خط $LINENO (exit $ec). لاگ: $INSTALL_LOG"' ERR

ACTION="install"
for a in "$@"; do
  case "$a" in
    --install) ACTION="install" ;;
    --update) ACTION="update" ;;
    --reinstall) ACTION="reinstall_keep" ;;
    --reinstall-full) ACTION="reinstall_full" ;;
    --uninstall) ACTION="uninstall" ;;
    --help|-h)
      echo "SakaBot installer"
      echo "  sudo bash install.sh [--install|--update|--reinstall|--reinstall-full|--uninstall|--help]"
      echo "  یا: bash <(curl -Ls URL) [--install|...]"
      echo "Env: INSTALL_DIR INSTALL_BRANCH INSTALL_DEV=true REPO_URL"
      exit 0
      ;;
  esac
done

: >"$INSTALL_LOG"
chmod 600 "$INSTALL_LOG" 2>/dev/null || true

require_root() {
  [[ "${EUID:-0}" -eq 0 ]] || die "با sudo اجرا کنید: sudo bash install.sh ..."
}

detect_ubuntu() {
  [[ -f /etc/os-release ]] || die "os-release یافت نشد"
  # shellcheck source=/dev/null
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "فقط Ubuntu پشتیبانی می‌شود (شما: ${ID:-unknown})"
  [[ "${VERSION_ID:-}" == "22.04" || "${VERSION_ID:-}" == "24.04" ]] || \
    die "نسخه پشتیبانی‌نشده: ${VERSION_ID:-?} — فقط 22.04 و 24.04"
  export VERSION_ID
  ok "Ubuntu ${VERSION_ID}"
}

install_apt_packages() {
  log "نصب بسته‌های سیستم..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y git curl wget nano unzip jq build-essential software-properties-common \
    postgresql postgresql-contrib nginx ufw htop ca-certificates openssl python3-pip
}

ensure_python311() {
  if command -v python3.11 &>/dev/null; then
    ok "python3.11 موجود است"
    return
  fi
  if [[ "${VERSION_ID}" == "22.04" ]]; then
    apt-get install -y software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
  fi
  apt-get install -y python3.11 python3.11-venv python3.11-dev
  ok "python3.11 نصب شد"
}

pg_create_role_and_db() {
  local pass="$1"
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='sakabot'" | grep -q 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER USER sakabot WITH PASSWORD '$(_pg_escape "$pass")';"
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE USER sakabot WITH PASSWORD '$(_pg_escape "$pass")';"
  fi
  if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='sakabot'" | grep -q 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE sakabot OWNER sakabot;"
  fi
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d sakabot -c "GRANT ALL ON SCHEMA public TO sakabot;" 2>/dev/null || true
  ok "PostgreSQL: کاربر/دیتابیس sakabot"
}

_pg_escape() {
  printf '%s' "$1" | sed "s/'/''/g"
}

encode_db_url_password() {
  python3.11 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"
}

write_full_env() {
  local bt="$1" oid="$2" pub="$3" brand="$4" sup="$5" dbp="$6" penc="$7"
  local enc
  enc="$(encode_db_url_password "$dbp")"
  cat >"${INSTALL_DIR}/.env" <<EOF
BOT_TOKEN=${bt}
OWNER_ID=${oid}
DATABASE_URL=postgresql+asyncpg://sakabot:${enc}@127.0.0.1:5432/sakabot
PUBLIC_BASE_URL=${pub}

BRAND_NAME=${brand}
SUPPORT_USERNAME=${sup}
TIMEZONE=Asia/Tehran
FOOTER_ENABLED=true

SUBSCRIPTION_ENDPOINT_ENABLED=true
SUB_BASE64_ENABLED=false
MULTI_BACKEND_ACTIVE=false

TRAFFIC_SYNC_INTERVAL_SECONDS=300
TRAFFIC_SYNC_BATCH_SIZE=100
TRAFFIC_SAFETY_BUFFER_MB=200

LOCATION_CHANGE_ENABLED=true
LOCATION_CHANGE_COOLDOWN_HOURS=24
LOCATION_CHANGE_MAX_PER_MONTH=3
LOCATION_CHANGE_REQUIRE_ADMIN_APPROVAL=false
LOCATION_CHANGE_FEE=0

API_PRODUCTS_ENABLED=true
MANUAL_MODE_ENABLED=true
ALLOW_USER_MANUAL_PRODUCTS=false
LEGACY_MANUAL_MODE=false

SHOW_FULL_CARD_NUMBER_TO_USER=true
DEBUG_CARD_LOGGING=false

DEBUG_MODE=false
LOG_LEVEL=INFO
LOG_TO_FILE=true
LOG_DIR=logs

AUTO_BACKUP_ENABLED=true
AUTO_BACKUP_INTERVAL_MINUTES=60
SEND_ENV_BACKUP=false
BACKUP_UNUSED_LINKS=false
BACKUP_RETENTION_HOURLY=48
BACKUP_RETENTION_DAILY=30

MIN_CHARGE_AMOUNT=10000
MAX_CHARGE_AMOUNT=50000000
LOW_STOCK_THRESHOLD=5
MAX_IMPORT_LINKS=1000

PANEL_CREDENTIAL_ENCRYPTION_KEY=${penc}
EOF
  chmod 600 "${INSTALL_DIR}/.env"
  ok ".env نوشته شد (BOT_TOKEN چاپ نشد)"
}

clone_or_pull() {
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "git pull در $INSTALL_DIR ..."
    git -C "$INSTALL_DIR" fetch origin "$INSTALL_BRANCH" || die "git fetch ناموفق — نصب فعلی دست‌نخورده ماند"
    git -C "$INSTALL_DIR" checkout "$INSTALL_BRANCH" 2>/dev/null || true
    git -C "$INSTALL_DIR" pull --ff-only origin "$INSTALL_BRANCH" || die "git pull ناموفق"
  else
    if [[ -e "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
      if [[ -f "${INSTALL_DIR}/.env" ]]; then
        warn "پوشه بدون git؛ .env نگه داشته می‌شود و مخزن کلون می‌شود"
        local old="${INSTALL_DIR}.preclone.$$"
        mv "$INSTALL_DIR" "$old"
        git clone --depth 1 --branch "$INSTALL_BRANCH" "$REPO_URL" "$INSTALL_DIR" || {
          git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
          git -C "$INSTALL_DIR" checkout "$INSTALL_BRANCH" || die "شاخه $INSTALL_BRANCH یافت نشد"
        }
        [[ -f "$old/.env" ]] && mv "$old/.env" "${INSTALL_DIR}/.env" && chmod 600 "${INSTALL_DIR}/.env"
        rm -rf "$old"
      else
        die "پوشه $INSTALL_DIR بدون git و بدون .env — خالی کنید یا INSTALL_DIR عوض کنید"
      fi
    else
      rm -rf "$INSTALL_DIR"
      log "git clone شاخه $INSTALL_BRANCH ..."
      git clone --depth 1 --branch "$INSTALL_BRANCH" "$REPO_URL" "$INSTALL_DIR" || {
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" checkout "$INSTALL_BRANCH" || die "شاخه $INSTALL_BRANCH یافت نشد"
      }
    fi
  fi
  ok "کد آماده است"
}

venv_install() {
  cd "$INSTALL_DIR"
  rm -rf .venv
  python3.11 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip setuptools wheel
  pip install -r requirements.txt
  if [[ "$INSTALL_DEV" == "true" ]] && [[ -f requirements-dev.txt ]]; then
    pip install -r requirements-dev.txt
  fi
  deactivate
  ok "pip install انجام شد"
}

run_migrations_script() {
  cd "$INSTALL_DIR"
  bash scripts/run_migrations.sh
  ok "Migration انجام شد"
}

compile_check() {
  cd "$INSTALL_DIR"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  set -a; source .env; set +a
  python -m compileall -q bot_app main.py
  python -c "from bot_app.config import get_settings; get_settings(); print('config: ok')"
  deactivate
}

delete_webhook_safe() {
  cd "$INSTALL_DIR"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  [[ -z "${BOT_TOKEN:-}" ]] && return
  curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" >/dev/null 2>&1 || true
  ok "deleteWebhook (در صورت نیاز)"
}

write_systemd() {
  cat > /etc/systemd/system/sakabot.service <<EOF
[Unit]
Description=SakaBot Telegram VPN Sales Bot
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python ${INSTALL_DIR}/main.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable sakabot.service
  ok "systemd sakabot.service"
}

stop_service_and_dupes() {
  systemctl stop sakabot.service 2>/dev/null || true
  pkill -f "${INSTALL_DIR}/.venv/bin/python.*${INSTALL_DIR}/main.py" 2>/dev/null || true
  sleep 1
}

start_service() {
  delete_webhook_safe
  systemctl restart sakabot.service
  sleep 2
  if systemctl is-active --quiet sakabot.service; then ok "سرویس sakabot فعال است"; else warn "سرویس بالا نیامد — journalctl -u sakabot -n 80"; fi
}

symlink_sakabot() {
  chmod +x "${INSTALL_DIR}/bot-manager.sh" "${INSTALL_DIR}/scripts/run_migrations.sh" 2>/dev/null || true
  ln -sf "${INSTALL_DIR}/bot-manager.sh" /usr/local/bin/sakabot
  ok "دستور: sakabot"
}

backup_db() {
  mkdir -p "${INSTALL_DIR}/backups"
  local f="${INSTALL_DIR}/backups/backup_$(date +%Y%m%d_%H%M%S).sql"
  sudo -u postgres pg_dump sakabot >"$f" 2>/dev/null && ok "بکاپ دیتابیس: $f" || warn "pg_dump رد شد"
}

prompt_if_dir_exists() {
  [[ ! -d "$INSTALL_DIR" ]] && return 0
  [[ ! -f "${INSTALL_DIR}/.env" && ! -d "${INSTALL_DIR}/.git" ]] && return 0
  echo -e "${Y}پوشه $INSTALL_DIR از قبل وجود دارد.${N}"
  echo "1) به‌روزرسانی (پیش‌فرض)  2) نصب مجدد، نگه‌داشتن .env و DB  3) نصب کامل (نیاز به تأیید)  4) انصراف"
  read -r -p "انتخاب [1]: " ch
  ch="${ch:-1}"
  case "$ch" in
    1) ACTION="update" ;;
    2) ACTION="reinstall_keep" ;;
    3) ACTION="reinstall_full" ;;
    4) exit 0 ;;
    *) ACTION="update" ;;
  esac
}

interactive_questions() {
  echo -n "ربات تلگرام BOT_TOKEN را وارد کنید: "
  read -r BOT_TOKEN
  [[ -n "${BOT_TOKEN:-}" ]] || die "BOT_TOKEN خالی است"
  echo -n "آیدی عددی مالک ربات (OWNER_ID): "
  read -r OWNER_ID
  [[ -n "${OWNER_ID:-}" ]] || die "OWNER_ID خالی است"
  echo -n "دامنه لینک ساب (مثال https://sub.example.com): "
  read -r PUBLIC_BASE_URL
  [[ -n "${PUBLIC_BASE_URL:-}" ]] || die "PUBLIC_BASE_URL خالی است"
  echo -n "نام برند [ساکانت]: "
  read -r BRAND_NAME
  BRAND_NAME="${BRAND_NAME:-ساکانت}"
  echo -n "یوزرنیم پشتیبانی (اختیاری، بدون @): "
  read -r SUPPORT_USERNAME
  echo -n "رمز PostgreSQL برای sakabot (خالی = تولید خودکار): "
  read -r -s DB_PASSWORD
  echo
  [[ -z "${DB_PASSWORD:-}" ]] && DB_PASSWORD="$(openssl rand -hex 16)" && echo "(رمز DB تولید شد — در .env ذخیره شد، چاپ نمی‌شود)"
  PANEL_KEY="$(openssl rand -hex 32)"
}

maybe_nginx() {
  local u="$1"
  if [[ "$u" =~ ^https://([^/]+) ]]; then
    local host="${BASH_REMATCH[1]}"
    echo -n "nginx برای /sub/ و /health به 127.0.0.1:8080 تنظیم شود؟ [y/N]: "
    read -r ngx
    if [[ "${ngx,,}" == "y" ]]; then
      cat > /etc/nginx/sites-available/sakabot-sub.conf <<NGX
server {
    listen 80;
    listen [::]:80;
    server_name ${host};
    location /sub/ {
        proxy_pass http://127.0.0.1:8080/sub/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    location /health {
        proxy_pass http://127.0.0.1:8080/health;
        proxy_set_header Host \$host;
    }
}
NGX
      ln -sf /etc/nginx/sites-available/sakabot-sub.conf /etc/nginx/sites-enabled/sakabot-sub.conf
      nginx -t && systemctl reload nginx
      ok "nginx"
      echo -n "نصب certbot و SSL (--nginx)؟ DNS باید به این سرور اشاره کند [y/N]: "
      read -r ssl
      if [[ "${ssl,,}" == "y" ]]; then
        apt-get install -y certbot python3-certbot-nginx
        echo -n "ایمیل برای Let's Encrypt: "
        read -r lemail
        echo -n "دامنه برای گواهی (مثال sub.example.com): "
        read -r ledom
        [[ -n "$ledom" ]] && certbot --nginx -d "$ledom" --non-interactive --agree-tos -m "${lemail:-admin@$ledom}" || warn "certbot نیاز به اجرای دستی دارد"
      fi
    fi
  else
    warn "بدون HTTPS دامنه، لینک ساب پایدار توصیه نمی‌شود — دامنه + HTTPS تنظیم کنید."
  fi
}

maybe_ufw() {
  echo -n "UFW (SSH, 80, 443) فعال شود؟ [y/N]: "
  read -r uf
  if [[ "${uf,,}" == "y" ]]; then
    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable || true
    ok "UFW"
  fi
}

print_final() {
  local pub=""
  [[ -f "${INSTALL_DIR}/.env" ]] && pub=$(grep '^PUBLIC_BASE_URL=' "${INSTALL_DIR}/.env" | cut -d= -f2- | tr -d '\r')
  echo ""
  echo -e "${G}✅ نصب با موفقیت انجام شد${N}"
  echo ""
  echo "مسیر نصب:     $INSTALL_DIR"
  echo "مدیریت:       sakabot"
  echo "وضعیت:       systemctl status sakabot"
  echo "لاگ زنده:    journalctl -u sakabot -f"
  echo "Health:       curl -sS http://127.0.0.1:8080/health"
  echo "لینک ساب:     ${pub}/sub/<token>"
  echo ""
  echo "مرحله بعد: /start در ربات، سپس /admin، کارت، پنل، پلن، خرید تست"
  echo "لاگ نصب:     $INSTALL_LOG"
}

do_update() {
  require_root
  detect_ubuntu
  install_apt_packages
  ensure_python311
  [[ -d "${INSTALL_DIR}/.git" ]] || die "نصب یافت نشد — ابتدا نصب کامل را اجرا کنید"
  backup_db
  clone_or_pull
  cd "$INSTALL_DIR"
  [[ -d .venv ]] || python3.11 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip setuptools wheel
  pip install -r requirements.txt
  deactivate
  run_migrations_script
  compile_check
  write_systemd
  stop_service_and_dupes
  start_service
  symlink_sakabot
  print_final
}

do_reinstall_keep() {
  require_root
  detect_ubuntu
  install_apt_packages
  ensure_python311
  [[ -f "${INSTALL_DIR}/.env" ]] || die ".env یافت نشد"
  backup_db
  clone_or_pull
  venv_install
  run_migrations_script
  compile_check
  write_systemd
  stop_service_and_dupes
  start_service
  symlink_sakabot
  print_final
}

do_reinstall_full() {
  echo -n "برای حذف فایل‌های برنامه و نصب مجدد، DELETE را تایپ کنید: "
  read -r c
  [[ "$c" == "DELETE" ]] || exit 1
  require_root
  detect_ubuntu
  install_apt_packages
  ensure_python311
  mkdir -p "${INSTALL_DIR}/backups"
  backup_db
  rm -rf "${INSTALL_DIR}/.venv" "${INSTALL_DIR}/bot_app" "${INSTALL_DIR}/main.py" 2>/dev/null || true
  interactive_questions
  mkdir -p "$INSTALL_DIR"
  clone_or_pull
  pg_create_role_and_db "$DB_PASSWORD"
  write_full_env "$BOT_TOKEN" "$OWNER_ID" "$PUBLIC_BASE_URL" "$BRAND_NAME" "${SUPPORT_USERNAME:-}" "$DB_PASSWORD" "$PANEL_KEY"
  venv_install
  run_migrations_script
  compile_check
  write_systemd
  maybe_nginx "$PUBLIC_BASE_URL"
  maybe_ufw
  stop_service_and_dupes
  start_service
  symlink_sakabot
  print_final
}

do_fresh_install() {
  require_root
  detect_ubuntu
  prompt_if_dir_exists
  case "$ACTION" in
    update) do_update; exit 0 ;;
    reinstall_keep) do_reinstall_keep; exit 0 ;;
    reinstall_full) do_reinstall_full; exit 0 ;;
  esac

  install_apt_packages
  ensure_python311
  interactive_questions
  mkdir -p "$INSTALL_DIR"
  clone_or_pull
  pg_create_role_and_db "$DB_PASSWORD"
  write_full_env "$BOT_TOKEN" "$OWNER_ID" "$PUBLIC_BASE_URL" "$BRAND_NAME" "${SUPPORT_USERNAME:-}" "$DB_PASSWORD" "$PANEL_KEY"
  venv_install
  run_migrations_script
  compile_check
  write_systemd
  maybe_nginx "$PUBLIC_BASE_URL"
  maybe_ufw
  stop_service_and_dupes
  start_service
  symlink_sakabot
  print_final
}

do_uninstall() {
  require_root
  echo "1) فقط سرویس  2) حذف فایل ربات، نگه‌داشتن DB  3) سرویس+فایل، نگه‌داشتن DB  4) حذف کامل شامل DB"
  read -r -p "انتخاب: " u
  echo -n "برای ادامه DELETE را تایپ کنید: "
  read -r d
  [[ "$d" == "DELETE" ]] || exit 1
  systemctl stop sakabot.service 2>/dev/null || true
  systemctl disable sakabot.service 2>/dev/null || true
  rm -f /etc/systemd/system/sakabot.service
  systemctl daemon-reload
  case "$u" in
    1) ;;
    2|3) rm -rf "${INSTALL_DIR}/.venv" "${INSTALL_DIR}/bot_app" "${INSTALL_DIR}/main.py" 2>/dev/null || true ;;
    4)
      sudo -u postgres psql -c "DROP DATABASE IF EXISTS sakabot;" 2>/dev/null || true
      sudo -u postgres psql -c "DROP ROLE IF EXISTS sakabot;" 2>/dev/null || true
      rm -rf "$INSTALL_DIR"
      ;;
  esac
  rm -f /usr/local/bin/sakabot
  ok "حذف انجام شد"
}

case "$ACTION" in
  update) do_update ;;
  reinstall_keep) do_reinstall_keep ;;
  reinstall_full) do_reinstall_full ;;
  uninstall) do_uninstall ;;
  install) do_fresh_install ;;
  *) do_fresh_install ;;
esac
