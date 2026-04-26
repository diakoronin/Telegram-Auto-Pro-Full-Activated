#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  DEFAULT_APP_USER="$SUDO_USER"
else
  DEFAULT_APP_USER="telegram-sender"
fi
APP_USER="${APP_USER:-$DEFAULT_APP_USER}"
APP_DIR="${APP_DIR:-/opt/telegram-sender}"
SERVICE_NAME="${SERVICE_NAME:-telegram-sender}"
REPO_URL="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
REPO_BRANCH="${REPO_BRANCH:-cursor/telegram-group-link-bot-6341}"

SEND_DELAY_SECONDS="${SEND_DELAY_SECONDS:-1.0}"
SCHEDULER_POLL_SECONDS="${SCHEDULER_POLL_SECONDS:-5}"
MAX_CONCURRENT_BROADCASTS="${MAX_CONCURRENT_BROADCASTS:-4}"
SERVICE_NOTIFY_OWNER="${SERVICE_NOTIFY_OWNER:-true}"
WEB_PANEL_ENABLED="${WEB_PANEL_ENABLED:-true}"
WEB_PANEL_HOST="${WEB_PANEL_HOST:-127.0.0.1}"
WEB_PANEL_PORT="${WEB_PANEL_PORT:-18080}"
WEB_PANEL_PATH="${WEB_PANEL_PATH:-}"
WEB_PANEL_USERNAME="${WEB_PANEL_USERNAME:-}"
WEB_PANEL_PASSWORD="${WEB_PANEL_PASSWORD:-}"
UFW_OPEN_PANEL="${UFW_OPEN_PANEL:-false}"
OWNER_ID="${OWNER_ID:-}"
STRICT_OWNER_ONLY="${STRICT_OWNER_ONLY:-true}"

ROOT_CMD=()
APP_USER_CMD=()
if [[ "${EUID}" -eq 0 ]]; then
  APP_USER_CMD=(su -s /bin/bash "$APP_USER" -c)
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Please run as root or install sudo."
    exit 1
  fi
  ROOT_CMD=(sudo)
  APP_USER_CMD=(sudo -u "$APP_USER" bash -lc)
fi

BOT_TOKEN="${BOT_TOKEN:-}"
if [[ -z "$BOT_TOKEN" ]]; then
  read -r -p "Enter BOT_TOKEN: " BOT_TOKEN
fi
if [[ -z "$BOT_TOKEN" ]]; then
  echo "BOT_TOKEN is required."
  exit 1
fi

if [[ "$APP_USER" == "root" ]]; then
  echo "APP_USER is root. This is not recommended."
  echo "Set APP_USER=telegram-sender for safer deployment."
fi

if [[ -z "$OWNER_ID" ]]; then
  read -r -p "Enter OWNER_ID (Telegram numeric user id): " OWNER_ID
fi
if [[ -z "$OWNER_ID" ]]; then
  echo "OWNER_ID is required."
  exit 1
fi

if [[ -z "$WEB_PANEL_PATH" ]]; then
  read -r -p "Enter WEB_PANEL_PATH (hidden URL segment, e.g. mypanel-84): " WEB_PANEL_PATH
fi
if [[ -z "$WEB_PANEL_PATH" ]]; then
  echo "WEB_PANEL_PATH is required."
  exit 1
fi
WEB_PANEL_PATH="$(printf "%s" "$WEB_PANEL_PATH" | tr -cd '[:alnum:]_-')"
if [[ -z "$WEB_PANEL_PATH" ]]; then
  echo "WEB_PANEL_PATH is invalid. Use only a-z A-Z 0-9 _ -"
  exit 1
fi

if [[ -z "$WEB_PANEL_USERNAME" ]]; then
  read -r -p "Enter WEB_PANEL_USERNAME: " WEB_PANEL_USERNAME
fi
if [[ -z "$WEB_PANEL_USERNAME" ]]; then
  echo "WEB_PANEL_USERNAME is required."
  exit 1
fi
if [[ -z "$WEB_PANEL_PASSWORD" ]]; then
  read -r -s -p "Enter WEB_PANEL_PASSWORD: " WEB_PANEL_PASSWORD
  echo
fi
if [[ -z "$WEB_PANEL_PASSWORD" ]]; then
  echo "WEB_PANEL_PASSWORD is required."
  exit 1
fi

echo "[1/8] Installing prerequisites..."
"${ROOT_CMD[@]}" apt-get update -y
"${ROOT_CMD[@]}" apt-get install -y python3 python3-venv python3-pip git

echo "[2/8] Preparing app directory..."
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  "${ROOT_CMD[@]}" useradd -m -s /bin/bash "$APP_USER"
fi
"${ROOT_CMD[@]}" mkdir -p "$APP_DIR"
"${ROOT_CMD[@]}" chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[3/8] Downloading or updating project..."
if [[ ! -d "$APP_DIR/.git" ]]; then
  "${APP_USER_CMD[@]}" "git clone --branch \"$REPO_BRANCH\" --single-branch \"$REPO_URL\" \"$APP_DIR\""
else
  "${APP_USER_CMD[@]}" "cd \"$APP_DIR\" && git fetch origin \"$REPO_BRANCH\" && git checkout \"$REPO_BRANCH\" && git pull origin \"$REPO_BRANCH\""
fi

echo "[4/8] Creating virtual environment and installing Python packages..."
"${APP_USER_CMD[@]}" "cd \"$APP_DIR\" && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt"

echo "[5/8] Writing environment configuration..."
"${ROOT_CMD[@]}" tee "$APP_DIR/.env" >/dev/null <<EOF
BOT_TOKEN=$BOT_TOKEN
DB_PATH=$APP_DIR/bot_data.sqlite3
SEND_DELAY_SECONDS=$SEND_DELAY_SECONDS
SCHEDULER_POLL_SECONDS=$SCHEDULER_POLL_SECONDS
MAX_CONCURRENT_BROADCASTS=$MAX_CONCURRENT_BROADCASTS
SERVICE_NOTIFY_OWNER=$SERVICE_NOTIFY_OWNER
WEB_PANEL_ENABLED=$WEB_PANEL_ENABLED
WEB_PANEL_HOST=$WEB_PANEL_HOST
WEB_PANEL_PORT=$WEB_PANEL_PORT
WEB_PANEL_PATH=$WEB_PANEL_PATH
WEB_PANEL_USERNAME=$WEB_PANEL_USERNAME
WEB_PANEL_PASSWORD=$WEB_PANEL_PASSWORD
OWNER_ID=$OWNER_ID
STRICT_OWNER_ONLY=$STRICT_OWNER_ONLY
EOF
"${ROOT_CMD[@]}" chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
"${ROOT_CMD[@]}" chmod 600 "$APP_DIR/.env"

echo "[6/8] Creating systemd service..."
"${ROOT_CMD[@]}" tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
[Unit]
Description=Telegram Sender Bot
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

echo "[7/8] Enabling and starting service..."
"${ROOT_CMD[@]}" systemctl daemon-reload
"${ROOT_CMD[@]}" systemctl enable "$SERVICE_NAME"
"${ROOT_CMD[@]}" systemctl restart "$SERVICE_NAME"

echo "[8/8] Optional firewall setup..."
if [[ "$UFW_OPEN_PANEL" == "true" ]]; then
  if command -v ufw >/dev/null 2>&1; then
    "${ROOT_CMD[@]}" ufw allow "$WEB_PANEL_PORT"/tcp
    echo "UFW rule added for port $WEB_PANEL_PORT"
  else
    echo "ufw not installed; skipping firewall rule."
  fi
else
  echo "Skipping firewall changes (UFW_OPEN_PANEL=false)."
fi

echo
echo "Install completed."
echo "Service: $SERVICE_NAME"
echo "Status:   sudo systemctl status $SERVICE_NAME"
echo "Logs:     sudo journalctl -u $SERVICE_NAME -f"
echo
echo "Web panel:"
if [[ "$WEB_PANEL_ENABLED" == "true" ]]; then
  echo "  URL:      http://<SERVER_IP>:$WEB_PANEL_PORT/$WEB_PANEL_PATH/login"
  echo "  Username: $WEB_PANEL_USERNAME"
  echo "  Password: (the one you entered)"
else
  echo "  disabled (WEB_PANEL_ENABLED=false)"
fi
