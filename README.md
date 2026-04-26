# Telegram sales bot (security-first)

## One-liner install (3x-ui style, must be **root**)

Use the branch that exists on GitHub (example: feature branch until merge to `main`):

```bash
sudo bash <(curl -Ls https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/cursor/telegram-sales-bot-security-712f/install.sh)
```

With custom install path and branch:

```bash
sudo bash <(curl -Ls https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/cursor/telegram-sales-bot-security-712f/install.sh) /opt/telegram-sales-bot main
```

Optional: `REPO=org/repo FRESH_DROP_DB=1` before `sudo` (see `scripts/fresh-install.sh`).

After install: `saka-bot status` / `saka-bot update`.

---

Python 3.11+ bot built with **aiogram 3** and **SQLAlchemy 2 (async)**. Wallet charges go through **pending payment requests** with **row-locked approve/reject**; purchases reserve a link with **`SELECT … FOR UPDATE`** (PostgreSQL: **`SKIP LOCKED`**) and update wallet in the **same transaction**.

## `saka-bot` CLI (server management)

After clone, link once:

```bash
sudo chmod +x /path/to/repo/scripts/saka-bot
sudo ln -sf /path/to/repo/scripts/saka-bot /usr/local/bin/saka-bot
# optional: custom install path
export SAKA_BOT_ROOT=/root/telegram-sales-bot
```

Commands (all ASCII):

| Command | Description |
|---------|-------------|
| `saka-bot update` | Same as `scripts/update.sh` — stop bot, `git pull`, `pip`, restart systemd if it was on |
| `saka-bot reinstall` | Delete `.venv`, recreate, `pip install -r requirements.txt`; restart if unit was active |
| `saka-bot restart` / `start` / `stop` / `status` | systemd (`SAKA_BOT_UNIT` or `SYSTEMD_UNIT` for service name) |
| `saka-bot logs` | Last 80 log lines; `saka-bot logs -f` follow |
| `saka-bot logs-save` | Writes last 500 lines to `logs/journal-snippet.txt` (for bug reports; folder gitignored) |
| `saka-bot diagnose` | Health check (no secrets printed) |
| `saka-bot db-info` | Prints DB **user**, host, port, database from `DATABASE_URL` (password never shown) |
| `sudo saka-bot db-password` | New Postgres password for that role + rewrite `DATABASE_URL` in `.env` |
| `saka-bot set-token` | Hidden prompt → `BOT_TOKEN` in `.env` |
| `saka-bot set-owner` | Prompt → `OWNER_ID` in `.env` (Telegram numeric id) |
| `saka-bot env-show` | Lists keys; masks token and DB URL |
| `saka-bot install` | Prints first-time install examples |
| `sudo saka-bot fresh BRANCH` | **Wipe install dir + unit file**, then latest `install.sh` + systemd + `saka-bot` link (needs root). Optional: `FRESH_DROP_DB=1` to drop old Postgres DB from `.env` before delete |

**Full reset from curl (root, SSH TTY):**

```bash
BRANCH="cursor/telegram-sales-bot-security-712f"
curl -fsSL "https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${BRANCH}/scripts/fresh-install.sh" | sudo bash -s -- /root/telegram-sales-bot "$BRANCH"
```

Optional: `FRESH_DROP_DB=1` before the pipe to also run `dropdb` on the database named in the old `.env`.

---

## Quick install / update (`scripts/quick.sh`)

On the server (SSH). If the install directory **already has** `scripts/update.sh`, it runs **`update.sh`** (stop bot → `git pull` → `pip` → restart). Otherwise it downloads **`install.sh`** with `sudo` (fresh install).

**Use the branch that actually exists on GitHub** (until merged, `main` may 404 for these scripts):

```bash
BRANCH="cursor/telegram-sales-bot-security-712f"
curl -fsSL "https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${BRANCH}/scripts/quick.sh" | bash -s -- "$HOME/telegram-sales-bot" "$BRANCH"
```

- First argument: install path (default in script: `~/telegram-sales-bot`).
- Second argument: git branch name.
- Other repo: `REPO_URL=https://github.com/org/repo.git` before `curl`.

Branches with **`/`** in the name: if `curl` returns **404**, clone that branch manually once, then from the repo folder run `bash scripts/update.sh`.

---

## One-line / full install on a Linux server (Debian/Ubuntu)

The **`scripts/install.sh`** script (run as **root** with `sudo`) will:

- Install **PostgreSQL** if missing and start it
- Create a **random** DB user, password, and database name
- Clone/update the repo, create **`.venv`**, `pip install -r requirements.txt`
- Ask only **`BOT_TOKEN`** (hidden) and **`OWNER_ID`** on the terminal, then write **`.env`**
- Save a copy of DB URL in **`.db_credentials`** (mode `600`) for backup
- `chown` the install tree to **`SUDO_USER`** when you used `sudo`

```bash
BRANCH="main"   # or your feature branch until merge
curl -fsSL "https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${BRANCH}/scripts/install.sh" | sudo bash -s -- /opt/telegram-sales-bot "$BRANCH"
```

Use an SSH session so **`/dev/tty`** exists for prompts. If you pipe without a TTY, set `NONINTERACTIVE=1` and fill `.env` manually.

Then (as the same user that owns the folder):

```bash
/opt/telegram-sales-bot/scripts/run.sh
```

Optional **systemd**:

```bash
cd /opt/telegram-sales-bot && sudo bash scripts/install-systemd.sh
```

### TelegramConflictError (getUpdates)

Only **one** process may poll Telegram with the same bot token. If you see `Conflict: terminated by other getUpdates request`:

```bash
sudo systemctl stop telegram-sales-bot.service
pkill -f "telegram-sales-bot/main.py" || true
# then start only one: either `python main.py` OR `systemctl start ...`, not both
```

If you use **webhook** elsewhere for this bot, switch that off or use a different bot token for this codebase.

### Update the bot on the server (pull + deps + safe restart)

Stops the systemd service (if running) and any manual `main.py` for this folder **before** `git pull` so Telegram does not see two pollers.

```bash
cd /root/telegram-sales-bot   # your install path
bash scripts/update.sh
```

- If you only use `python main.py` (no systemd), the script still kills that process before pull, then tells you to start again.
- `SKIP_SYSTEMD=1 bash scripts/update.sh` — only git + pip, no systemctl.
- `SKIP_PIP=1 bash scripts/update.sh` — only git pull + restart.

Requires `sudo` for `systemctl` when not root (or run the whole script as root).

If the bot **does not start**, run (from the install directory, as the project owner):

```bash
bash scripts/diagnose.sh
# or: bash scripts/diagnose.sh /opt/telegram-sales-bot
```

It checks `.env`, imports, Postgres port, DB `SELECT 1`, and Telegram `getMe` **without printing secrets**.

Override clone URL: `REPO_URL=https://github.com/you/fork.git curl ... | sudo bash -s -- /opt/bot main`

## Quick start (manual)

1. Copy `.env.example` to `.env` and fill variables (never commit `.env`).
2. Use an async database URL, for example:
   - PostgreSQL (recommended): `postgresql+asyncpg://user:pass@host:5432/dbname`
   - Local SQLite: `sqlite+aiosqlite:///./data.db`
3. Install and run:

```bash
pip install -r requirements.txt
python3 main.py
```

The first run creates tables and applies lightweight **additive migrations** (for example the `plans.low_stock_rearm` column on existing databases). The Telegram user matching `OWNER_ID` becomes an **owner** admin automatically.

## Security notes

- **Roles** are enforced from the database on every sensitive path; Telegram callback data carries only short IDs (e.g. payment request id, confirmation id).
- **Blocked users** cannot use shop, wallet charge, receipts, support, or receive deliveries (admins are exempt from the block middleware so they can work).
- **Payment approval** uses `FOR UPDATE` on the payment row and refuses non-`pending` states; user notification runs **after** successful commit.
- **Wallet** changes always append a `wallet_transactions` row with `balance_before` / `balance_after`; balance is constrained non-negative at the DB layer.
- **Secrets** load only from environment; the bot token is never logged by application code.
- **Low stock**: when unused links for an active plan drop to `LOW_STOCK_THRESHOLD` or below (after a delivery, import, or delete-unused), **owner and manager** receive one Telegram alert per cycle; restocking above the threshold **re-arms** the next alert.
- **Payment card visibility**: active card numbers are **not** shown to every user. **Owner or manager** must grant `card_view_allowed` from **Admin → “Card access for user”**: **forward a message** from that user (sender must be visible) **or** enter their **numeric Telegram ID**, then confirm. Until then the cards button is hidden and the charge success message does not list cards. Access can be **revoked** the same way.

See [SECURITY.md](SECURITY.md) for the full threat model and deployment checklist.

## Roles (summary)

| Role    | Capabilities (high level) |
|---------|---------------------------|
| owner   | Full control: admins, cards, dangerous confirmations, full backup, refunds, link return |
| manager | Approve/reject payments, reports, user block, plans/servers (deactivate), link import, wallet adjust |
| seller  | Manual link delivery only |
| user    | Buy with wallet, charge flow, own history, support |

## Environment variables

Required and optional keys are documented in `.env.example`.

## License

See `LICENSE` in the repository.
