# Telegram sales bot (security-first)

Python 3.11+ bot built with **aiogram 3** and **SQLAlchemy 2 (async)**. Wallet charges go through **pending payment requests** with **row-locked approve/reject**; purchases reserve a link with **`SELECT … FOR UPDATE`** (PostgreSQL: **`SKIP LOCKED`**) and update wallet in the **same transaction**.

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
- **Payment card visibility**: active card numbers are **not** shown to every user. A user must have `card_view_allowed` set by **owner or manager** (via **Admin → دسترسی کارت برای کاربر**): either **forward a message from that user** to the bot (sender must be visible) or enter their **numeric Telegram ID**, then confirm. Until then, the «شماره کارت‌ها» button is hidden and the charge flow does not append card lines. Access can be **revoked** the same way.

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
