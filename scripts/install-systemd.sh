#!/usr/bin/env bash
# Optional: install systemd unit for the bot (run with sudo).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_NAME="${UNIT_NAME:-telegram-sales-bot.service}"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo root)}}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-systemd.sh" >&2
  exit 1
fi

cat >"/etc/systemd/system/$UNIT_NAME" <<EOF
[Unit]
Description=Telegram sales bot (aiogram)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$ROOT/.venv/bin/python $ROOT/main.py
Restart=on-failure
RestartSec=12

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$UNIT_NAME"
systemctl restart "$UNIT_NAME"
echo "Installed and started: $UNIT_NAME"
systemctl status "$UNIT_NAME" --no-pager || true
