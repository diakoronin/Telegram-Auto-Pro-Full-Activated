# Telegram sales bot (security-first)

Python 3.11+ bot built with **aiogram 3** and **SQLAlchemy 2 (async)**. Wallet charges go through **pending payment requests** with **row-locked approve/reject**; purchases reserve a link with **`SELECT … FOR UPDATE`** (PostgreSQL: **`SKIP LOCKED`**) and update wallet in the **same transaction**.

## One-line install on a Linux server (like panel scripts)

From the server (Debian/Ubuntu: installs `python3-venv`, `pip`, `git` via `apt` if needed):

```bash
# After this branch is merged into main, use main. Until then use your branch name:
BRANCH="cursor/telegram-sales-bot-security-712f"
curl -fsSL "https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/${BRANCH}/scripts/install.sh" | bash -s -- "$HOME/telegram-sales-bot" "$BRANCH"
```

- First argument: install directory (default `~/telegram-sales-bot`).
- Second argument: git branch (e.g. `main` once merged).
- Override clone URL: `REPO_URL=https://github.com/you/fork.git curl ... | bash -s -- /opt/bot main`

Then edit `.env` and start:

```bash
nano ~/telegram-sales-bot/.env
~/telegram-sales-bot/scripts/run.sh
```

Optional **systemd** (runs as the user who invoked `sudo`, uses `.env`):

```bash
cd ~/telegram-sales-bot && sudo bash scripts/install-systemd.sh
```

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
