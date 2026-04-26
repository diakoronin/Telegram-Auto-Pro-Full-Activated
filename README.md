# Telegram sales bot (security-first)

Python 3.11+ bot built with **aiogram 3** and **SQLAlchemy 2 (async)**. Wallet charges go through **pending payment requests** with **row-locked approve/reject**; purchases reserve a link with **`SELECT … FOR UPDATE`** (PostgreSQL: **`SKIP LOCKED`**) and update wallet in the **same transaction**.

## Quick start

1. Copy `.env.example` to `.env` and fill variables (never commit `.env`).
2. Use an async database URL, for example:
   - PostgreSQL (recommended): `postgresql+asyncpg://user:pass@host:5432/dbname`
   - Local SQLite: `sqlite+aiosqlite:///./data.db`
3. Install and run:

```bash
pip install -r requirements.txt
python3 main.py
```

The first run creates tables. The Telegram user matching `OWNER_ID` becomes an **owner** admin automatically.

## Security notes

- **Roles** are enforced from the database on every sensitive path; Telegram callback data carries only short IDs (e.g. payment request id, confirmation id).
- **Blocked users** cannot use shop, wallet charge, receipts, support, or receive deliveries (admins are exempt from the block middleware so they can work).
- **Payment approval** uses `FOR UPDATE` on the payment row and refuses non-`pending` states; user notification runs **after** successful commit.
- **Wallet** changes always append a `wallet_transactions` row with `balance_before` / `balance_after`; balance is constrained non-negative at the DB layer.
- **Secrets** load only from environment; the bot token is never logged by application code.

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
