#!/usr/bin/env bash
# One-shot install: clone (or update) repo, venv, pip deps, .env scaffold.
# Usage (on server):
#   curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/BRANCH/scripts/install.sh" | bash -s -- [INSTALL_DIR] [BRANCH]
# Or after clone:
#   bash scripts/install.sh [INSTALL_DIR]
#
# Environment overrides:
#   REPO_URL   — default: https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git
#   BRANCH     — default: main (second CLI arg wins if passed)

set -euo pipefail

DEFAULT_REPO="${REPO_URL:-https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git}"
BRANCH="${2:-${BRANCH:-main}}"
INSTALL_DIR="${1:-${INSTALL_DIR:-$HOME/telegram-sales-bot}}"

log() { printf '%s\n' "[install] $*"; }
die() { printf '%s\n' "[install] ERROR: $*" >&2; exit 1; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"; }

ensure_debian_python() {
  if [[ -f /etc/debian_version ]] && command -v apt-get >/dev/null 2>&1; then
    if ! dpkg -s python3-venv >/dev/null 2>&1 || ! dpkg -s python3-pip >/dev/null 2>&1; then
      log "Installing python3, venv, pip, git via apt (needs sudo)..."
      sudo apt-get update -qq
      sudo apt-get install -y python3 python3-venv python3-pip git ca-certificates curl
    fi
  fi
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

main() {
  need_cmd git
  need_cmd curl

  if ! PY="$(pick_python)"; then
    log "Python 3.11+ not found."
    ensure_debian_python
    PY="$(pick_python)" || die "Install Python 3.11 or newer (e.g. apt install python3.11 python3.11-venv)."
  fi
  log "Using: $($PY --version)"

  PARENT="$(dirname "$INSTALL_DIR")"
  NAME="$(basename "$INSTALL_DIR")"
  mkdir -p "$PARENT"

  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log "Updating existing clone: $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull origin "$BRANCH"
  else
    log "Cloning $DEFAULT_REPO (branch $BRANCH) → $INSTALL_DIR"
    git clone --depth 1 --branch "$BRANCH" "$DEFAULT_REPO" "$INSTALL_DIR"
  fi

  cd "$INSTALL_DIR"
  if [[ ! -f requirements.txt ]]; then
    die "requirements.txt missing — wrong repo or branch?"
  fi

  log "Creating venv: .venv"
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip wheel setuptools
  pip install -r requirements.txt

  if [[ ! -f .env ]]; then
    cp -n .env.example .env 2>/dev/null || cp .env.example .env
    log "Created .env from .env.example — edit it before starting:"
    log "  nano $INSTALL_DIR/.env"
  else
    log ".env already exists; not overwriting."
  fi

  log "Done."
  log "Edit secrets: $INSTALL_DIR/.env"
  log "Run once:     $INSTALL_DIR/scripts/run.sh"
  log "Or systemd:   sudo bash $INSTALL_DIR/scripts/install-systemd.sh"
}

main "$@"
