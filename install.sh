#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
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
WEB_PANEL_PORT="${WEB_PANEL_PORT:-8080}"
UFW_OPEN_PANEL="${UFW_OPEN_PANEL:-false}"

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    echo "Please run as root or install sudo."
    exit 1
  fi
fi

BOT_TOKEN="${BOT_TOKEN:-}"
if [[ -z "$BOT_TOKEN" ]]; then
  read -r -p "Enter BOT_TOKEN: " BOT_TOKEN
fi
if [[ -z "$BOT_TOKEN" ]]; then
  echo "BOT_TOKEN is required."
  exit 1
fi

WEB_PANEL_TOKEN="${WEB_PANEL_TOKEN:-}"
if [[ -z "$WEB_PANEL_TOKEN" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    WEB_PANEL_TOKEN="$(openssl rand -hex 24)"
  else
    WEB_PANEL_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(24))
PY
)"
  fi
fi

echo "[1/8] Installing prerequisites..."
$SUDO apt-get update -y
$SUDO apt-get install -y python3 python3-venv python3-pip git

echo "[2/8] Preparing app directory..."
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  $SUDO useradd -m -s /bin/bash "$APP_USER"
fi
$SUDO mkdir -p "$APP_DIR"
$SUDO chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "[3/8] Downloading or updating project..."
if [[ ! -d "$APP_DIR/.git" ]]; then
  $SUDO -u "$APP_USER" git clone --branch "$REPO_BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
else
  $SUDO -u "$APP_USER" bash -lc "cd \"$APP_DIR\" && git fetch origin \"$REPO_BRANCH\" && git checkout \"$REPO_BRANCH\" && git pull origin \"$REPO_BRANCH\""
fi

echo "[4/8] Creating virtual environment and installing Python packages..."
$SUDO -u "$APP_USER" bash -lc "cd \"$APP_DIR\" && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt"

echo "[5/8] Writing environment configuration..."
$SUDO tee "$APP_DIR/.env" >/dev/null <<EOF
BOT_TOKEN=$BOT_TOKEN
DB_PATH=$APP_DIR/bot_data.sqlite3
SEND_DELAY_SECONDS=$SEND_DELAY_SECONDS
SCHEDULER_POLL_SECONDS=$SCHEDULER_POLL_SECONDS
MAX_CONCURRENT_BROADCASTS=$MAX_CONCURRENT_BROADCASTS
SERVICE_NOTIFY_OWNER=$SERVICE_NOTIFY_OWNER
WEB_PANEL_ENABLED=$WEB_PANEL_ENABLED
WEB_PANEL_HOST=$WEB_PANEL_HOST
WEB_PANEL_PORT=$WEB_PANEL_PORT
WEB_PANEL_TOKEN=$WEB_PANEL_TOKEN
EOF
$SUDO chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
$SUDO chmod 600 "$APP_DIR/.env"

echo "[6/8] Creating systemd service..."
$SUDO tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<EOF
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
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE_NAME"
$SUDO systemctl restart "$SERVICE_NAME"

echo "[8/8] Optional firewall setup..."
if [[ "$UFW_OPEN_PANEL" == "true" ]]; then
  if command -v ufw >/dev/null 2>&1; then
    $SUDO ufw allow "$WEB_PANEL_PORT"/tcp
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
  echo "  http://<SERVER_IP>:$WEB_PANEL_PORT/?token=$WEB_PANEL_TOKEN"
else
  echo "  disabled (WEB_PANEL_ENABLED=false)"
fi
