#!/usr/bin/env bash
# Full server install: Postgres (Debian/Ubuntu), random DB user/password, venv, .env.
# Only asks: BOT_TOKEN and OWNER_ID (from terminal, safe with curl|bash).
#
#   curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/BRANCH/scripts/install.sh" | sudo bash -s -- [INSTALL_DIR] [BRANCH]
#
# Must run with sudo for Postgres + recommended for /opt installs.
# Env:
#   REPO_URL  — git clone URL
#   BRANCH    — default main; second CLI arg overrides
#   NONINTERACTIVE=1 — skip prompts (for CI); leaves .env partial

set -euo pipefail

DEFAULT_REPO="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
BRANCH="${2:-${BRANCH:-main}}"
INSTALL_DIR="${1:-${INSTALL_DIR:-$HOME/telegram-sales-bot}}"

log() { printf '%s\n' "[install] $*"; }
die() { printf '%s\n' "[install] ERROR: $*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

ensure_sudo() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "Run as root so Postgres can be installed. Example: curl ... | sudo bash -s -- /opt/telegram-sales-bot main"
  fi
}

ensure_debian_python() {
  if [[ -f /etc/debian_version ]] && command -v apt-get >/dev/null 2>&1; then
    if ! dpkg -s python3-venv >/dev/null 2>&1 || ! dpkg -s python3-pip >/dev/null 2>&1; then
      log "Installing python3, venv, pip, git..."
      apt-get update -qq
      apt-get install -y python3 python3-venv python3-pip git ca-certificates curl openssl
    fi
  fi
}

ensure_postgres() {
  if [[ ! -f /etc/debian_version ]] || ! command -v apt-get >/dev/null 2>&1; then
    die "Auto-Postgres only supports Debian/Ubuntu. Install Postgres manually and set DATABASE_URL in .env."
  fi
  if ! dpkg -s postgresql >/dev/null 2>&1; then
    log "Installing PostgreSQL..."
    apt-get update -qq
    apt-get install -y postgresql postgresql-contrib
  fi
  systemctl enable postgresql
  systemctl start postgresql
  sleep 1
  if ! systemctl is-active --quiet postgresql; then
    die "PostgreSQL failed to start. Check: systemctl status postgresql"
  fi
  log "PostgreSQL is running."
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

pick_python() {
  for c in python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && python_ok "$c"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

# Postgres identifiers: alphanumeric + underscore only (from openssl hex).
gen_db_user() { echo "tsb_u_$(openssl rand -hex 5)"; }
gen_db_name() { echo "tsb_db_$(openssl rand -hex 4)"; }
# URL-safe password (alphanumeric)
gen_db_pass() { openssl rand -base64 32 | tr -d '+/=\n' | head -c 28; }

create_db_role() {
  local db_user="$1" db_pass="$2" db_name="$3"
  local ep="${db_pass//\'/\'\'}"
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${db_user}'" | grep -q 1; then
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER ROLE \"${db_user}\" WITH PASSWORD '${ep}';" \
      || die "Failed to ALTER Postgres role"
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE ROLE \"${db_user}\" LOGIN PASSWORD '${ep}';" \
      || die "Failed to CREATE Postgres role"
  fi
  if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${db_name}'" | grep -q 1; then
    log "Database ${db_name} already exists; setting owner."
    sudo -u postgres psql -c "ALTER DATABASE \"${db_name}\" OWNER TO \"${db_user}\";" || true
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${db_name}\" OWNER \"${db_user}\";" \
      || die "Failed to CREATE DATABASE"
  fi
}

write_env() {
  local install_dir="$1" bot_token="$2" owner_id="$3" database_url="$4"
  local py
  py="$(command -v python3 || command -v python)"
  BOT_TOKEN="$bot_token" OWNER_ID="$owner_id" DATABASE_URL="$database_url" INSTALL_DIR="$install_dir" "$py" <<'PY'
import os
from pathlib import Path

root = Path(os.environ["INSTALL_DIR"])
ex = root / ".env.example"
out = root / ".env"
text = ex.read_text(encoding="utf-8")
repl = {
    "BOT_TOKEN": os.environ["BOT_TOKEN"],
    "OWNER_ID": os.environ["OWNER_ID"],
    "DATABASE_URL": os.environ["DATABASE_URL"],
}
lines = []
for line in text.splitlines():
    if not line.strip() or line.lstrip().startswith("#"):
        lines.append(line)
        continue
    if "=" not in line:
        lines.append(line)
        continue
    k, _, _ = line.partition("=")
    k = k.strip()
    if k in repl:
        lines.append(f"{k}={repl[k]}")
    else:
        lines.append(line)
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
out.chmod(0o600)
PY
}

main() {
  ensure_sudo
  need_cmd git
  need_cmd curl
  need_cmd openssl

  if ! PY="$(pick_python)"; then
    log "Python 3.11+ not found."
    ensure_debian_python
    PY="$(pick_python)" || die "Install Python 3.11+."
  fi
  log "Using: $($PY --version)"

  PARENT="$(dirname "$INSTALL_DIR")"
  mkdir -p "$PARENT"

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating: $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH" 2>/dev/null || true
    git -C "$INSTALL_DIR" checkout "$BRANCH" 2>/dev/null || true
    git -C "$INSTALL_DIR" pull origin "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" pull --ff-only || true
  else
    log "Cloning → $INSTALL_DIR"
    git clone --depth 1 --branch "$BRANCH" "$DEFAULT_REPO" "$INSTALL_DIR"
  fi

  cd "$INSTALL_DIR"
  [[ -f requirements.txt ]] || die "requirements.txt missing"

  ensure_postgres

  DB_USER="$(gen_db_user)"
  DB_NAME="$(gen_db_name)"
  DB_PASS="$(gen_db_pass)"
  log "Creating Postgres role and database (random credentials)..."
  create_db_role "$DB_USER" "$DB_PASS" "$DB_NAME"

  # URL-encode minimal: our user/pass are alphanumeric + underscore; if pass has % encode — gen_db_pass avoids specials
  DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}"

  CREDS_FILE="$INSTALL_DIR/.db_credentials"
  umask 077
  {
    echo "Postgres (keep this file secret; chmod 600)"
    echo "DATABASE_URL=${DATABASE_URL}"
    echo "DB_USER=${DB_USER}"
    echo "DB_NAME=${DB_NAME}"
    echo "DB_PASS=${DB_PASS}"
  } > "$CREDS_FILE"
  chmod 600 "$CREDS_FILE"
  log "Saved DB connection copy to: $CREDS_FILE (root-only)"

  log "Creating venv..."
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -q -U pip wheel setuptools
  pip install -q -r requirements.txt

  BOT_TOKEN=""
  OWNER_ID=""
  if [[ "${NONINTERACTIVE:-}" == "1" ]]; then
    log "NONINTERACTIVE=1: not prompting; fill .env manually."
    cp -f .env.example .env
    chmod 600 .env
  else
    log "Enter bot credentials (input hidden for token):"
    printf 'BOT_TOKEN: ' > /dev/tty
    read -rs BOT_TOKEN < /dev/tty || true
    printf '\n' > /dev/tty
    [[ -n "${BOT_TOKEN:-}" ]] || die "BOT_TOKEN empty"
    printf 'OWNER_ID (your numeric Telegram user id): ' > /dev/tty
    read -r OWNER_ID < /dev/tty || true
    [[ -n "${OWNER_ID:-}" ]] || die "OWNER_ID empty"
    write_env "$INSTALL_DIR" "$BOT_TOKEN" "$OWNER_ID" "$DATABASE_URL"
    log "Wrote $INSTALL_DIR/.env"
  fi

  if [[ -n "${SUDO_USER:-}" ]] && id "$SUDO_USER" &>/dev/null; then
    chown -R "$SUDO_USER:$SUDO_USER" "$INSTALL_DIR"
    log "Ownership set to $SUDO_USER (for run/systemd without root)."
  fi

  log ""
  log "=== Done ==="
  log "Install dir: $INSTALL_DIR"
  log "Start bot:   $INSTALL_DIR/scripts/run.sh"
  log "Systemd:     bash $INSTALL_DIR/scripts/install-systemd.sh"
  log "DB backup:   cat $CREDS_FILE   (as root only)"
}

main "$@"
