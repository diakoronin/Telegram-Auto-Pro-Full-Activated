#!/usr/bin/env bash
# Check why the bot fails to start (no secrets printed).
set -euo pipefail

ROOT="${1:-${INSTALL_DIR:-$HOME/telegram-sales-bot}}"
cd "$ROOT" 2>/dev/null || { echo "Directory not found: $ROOT"; exit 1; }

echo "=== Telegram sales bot diagnose ==="
echo "ROOT=$ROOT"
echo

if [[ ! -f .env ]]; then
  echo "[FAIL] .env missing. Run install.sh or copy .env.example to .env"
  exit 1
fi
echo "[OK] .env exists"

mask() {
  local s="$1" n="${#1}"
  if (( n <= 8 )); then echo "***"; return; fi
  echo "${s:0:4}...${s: -4} (len=$n)"
}

while IFS= read -r line; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue
  if [[ "$line" =~ ^BOT_TOKEN= ]]; then
    v="${line#BOT_TOKEN=}"
    echo "BOT_TOKEN=$(mask "$v")"
  elif [[ "$line" =~ ^OWNER_ID= ]]; then
    echo "$line"
  elif [[ "$line" =~ ^DATABASE_URL= ]]; then
    v="${line#DATABASE_URL=}"
    if [[ "$v" == *"@"* ]]; then
      echo "DATABASE_URL=${v%%@*}@***"
    else
      echo "DATABASE_URL=(set)"
    fi
  elif [[ "$line" =~ ^SUPPORT_USERNAME= ]]; then
    echo "$line"
  fi
done < .env

echo
if [[ ! -d .venv ]]; then
  echo "[FAIL] .venv missing — run: bash scripts/install.sh"
  exit 1
fi
echo "[OK] .venv exists"

if ! .venv/bin/python -c "import aiogram, sqlalchemy" 2>/dev/null; then
  echo "[FAIL] venv deps missing — run: .venv/bin/pip install -r requirements.txt"
  exit 1
fi
echo "[OK] Python deps import"

echo
echo "--- load_settings() ---"
if ! .venv/bin/python - <<'PY'
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from dotenv import load_dotenv
load_dotenv(Path.cwd() / ".env")
try:
    from app.config import load_settings
    load_settings()
    print("OK: all required env vars present")
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:200])
    sys.exit(1)
PY
then
  echo "[FAIL] Config check failed (see above)"
  exit 1
fi

echo
echo "--- PostgreSQL (if DATABASE_URL uses postgres) ---"
if grep -q '^DATABASE_URL=postgresql' .env 2>/dev/null; then
  if systemctl is-active --quiet postgresql 2>/dev/null; then
    echo "[OK] postgresql service is active"
  else
    echo "[WARN] postgresql service not active — run: sudo systemctl start postgresql"
  fi
  if command -v ss >/dev/null; then
    if ss -tlnp 2>/dev/null | grep -q ':5432'; then
      echo "[OK] something listens on :5432"
    else
      echo "[WARN] nothing listening on :5432"
    fi
  fi
else
  echo "(skipped — not postgresql URL)"
fi

echo
echo "--- DB connect (8s timeout) ---"
if ! .venv/bin/python - <<'PY'
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.cwd() / ".env")
url = os.getenv("DATABASE_URL", "")
if not url.startswith("postgresql"):
    print("SKIP: not async postgres URL")
    sys.exit(0)
try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
except ImportError as e:
    print("FAIL import", e)
    sys.exit(1)

async def main():
    eng = create_async_engine(url, pool_pre_ping=True)
    try:
        async with eng.connect() as c:
            await c.execute(text("SELECT 1"))
        print("OK: database accepts connection")
    finally:
        await eng.dispose()

asyncio.run(asyncio.wait_for(main(), timeout=8))
PY
then
  echo "[FAIL] Database connection failed"
  exit 1
fi

echo
echo "--- Telegram API (getMe) ---"
if ! .venv/bin/python - <<'PY'
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path.cwd() / ".env")
token = os.getenv("BOT_TOKEN", "").strip()
if not token:
    print("FAIL: no BOT_TOKEN")
    sys.exit(1)

async def main():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    b = Bot(token, default=DefaultBotProperties())
    try:
        me = await b.get_me()
        print("OK: Telegram API ok, bot @%s" % (me.username or me.id))
    finally:
        await b.session.close()

asyncio.run(asyncio.wait_for(main(), timeout=15))
PY
then
  echo "[FAIL] Telegram getMe failed (wrong token or network/firewall)"
  exit 1
fi

echo
echo "=== All checks passed. Try: ==="
echo "  $ROOT/scripts/run.sh"
echo "  sudo journalctl -u telegram-sales-bot.service -n 50 --no-pager"
