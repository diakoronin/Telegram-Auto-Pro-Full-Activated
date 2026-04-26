#!/usr/bin/env bash
# Run database migrations (uses .env DATABASE_URL). Idempotent.
set -euo pipefail
INSTALL_DIR="${INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$INSTALL_DIR"
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found in $INSTALL_DIR" >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  echo "ERROR: .venv not found. Run installer or: python3.11 -m venv .venv" >&2
  exit 1
fi
set -a
# shellcheck source=/dev/null
source .env
set +a
exec .venv/bin/python - <<'PY'
import asyncio
import os
from bot_app.config import clear_settings_cache, get_settings
from bot_app.db.session import get_engine, reset_engine
from bot_app.migrations.runner import run_migrations

async def main():
    clear_settings_cache()
    reset_engine()
    s = get_settings()
    eng = get_engine(s.database_url)
    await run_migrations(eng)
    await eng.dispose()

asyncio.run(main())
PY
