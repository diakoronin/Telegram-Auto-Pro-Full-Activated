# Security model

This document describes how the bot implements the security requirements: access control, payment safety, wallet integrity, link delivery locking, backups, and safe operations.

## Roles and permissions

Roles are stored in the `admins` table (`owner`, `manager`, `seller`). The Telegram user configured as `OWNER_ID` is ensured to exist as an **owner** admin on each request context.

- **Owner**: payment cards, admin lifecycle, role assignment, full database backup export, purchase refund, returning a consumed link to unused inventory, confirmations for destructive actions that only the owner should finalize where applicable.
- **Manager**: approve/reject wallet top-up requests, deactivate servers/plans, bulk link import, delete unused links for a plan, block users, manual wallet adjustment (large adjustments require owner — enforced in service logic), text report export.
- **Seller**: only the manual link delivery flow (pick server/plan, deliver one unused link inside a transaction).
- **User** (no admin row): shop, wallet balance, charge (receipt flow), own purchase/payment history, support message.

**Rule:** handlers check `admin.role` from the database; callback payloads are never trusted for authorization.

## Payment approval safety

- Each `payment_requests` row moves from `pending` to `approved` or `rejected` **once**. Approve/reject paths load the row with **`SELECT … FOR UPDATE`** inside the same session/transaction as wallet updates.
- If status is not `pending`, the operation is rejected.
- Fields `reviewed_by_admin_id`, `reviewed_at`, `rejection_reason` (on reject), and `status` are persisted in the same transaction as the wallet credit (approve only).
- The user receives Telegram notifications **only in `after_commit` hooks** so a failed transaction never sends a false success message.

## Wallet transaction safety

- `users.wallet_balance` has a **check constraint** (`>= 0`).
- Every credit/debit uses `wallet_transactions` with `balance_before`, `balance_after`, typed reason, and optional foreign keys to `payment_requests` / `purchases`.
- Manual adjustment requires a **text reason** and **manager or owner**; amounts whose absolute value is ≥ `LARGE_WALLET_ADJUSTMENT_AMOUNT` require **owner**.
- Approve payment and purchase flows never mutate balance outside ORM operations within the active transaction.

## Link delivery locking

- Unused links are selected with **`FOR UPDATE`**. On PostgreSQL, **`SKIP LOCKED`** avoids admins contending on the same row; SQLite uses `FOR UPDATE` only (single-writer semantics for typical deployments).
- **User purchase:** lock user row → pick link → deduct wallet → mark link used → insert purchase, wallet transaction, delivery — single commit.
- **Manual admin delivery:** pick and lock one unused link, mark used, insert delivery — no wallet movement.
- **Return link (owner):** locks the link row, allows transition from `used` back to `unused` for operational correction (audit logged).

## Backup safety

- **Full backup** export is owner-only, requires explicit confirmation, is **audit-logged**, uses a **timestamped filename**, and sends the file via Telegram; the temporary file is deleted after send when possible.
- **Managers** may export a short numeric **report** only (counts), not raw link inventory.

## Callback and confirmation IDs

- Sensitive admin actions use `pending_confirmations` rows: the callback carries only `acf:<id>` or payment actions `ap:<id>` / `rj:<id>`.
- The server loads the confirmation or payment entity from the database, checks **expiry**, **admin telegram id**, and **role**, then performs the transition.

## Recommended VPS deployment security

- Run the bot under a dedicated Linux user with **no shell login**; use `systemd` with `EnvironmentFile=/path/to/.env` (mode `0600`, owner root or the service user).
- Use **PostgreSQL** in production; restrict network access to the DB; enable TLS to the database if remote.
- Keep **Python and dependencies patched**; pin versions in `requirements.txt` and rebuild images regularly.
- **Firewall**: only outbound HTTPS to `api.telegram.org` (and your DB port on a private network).
- **Logs**: ship logs to a centralized system; never enable echo of SQL with secrets in production.
- **Backups**: store exported files encrypted at rest; limit who can read Telegram owner chat history.

## Payment card visibility (per-user gate)

- Active `payment_cards` rows are sensitive. Regular users only see masked numbers **after** `users.card_view_allowed` is `true`.
- **Owner or manager** grants access from the admin panel (**Card access for user**): confirm via a forwarded user message (real `sender_user` required — anonymous forward is rejected) **or** by numeric Telegram user id, then **two-step confirmation** (`acf:`) and audit (`card_view_granted` / `card_view_revoked`).
- Revocation clears the flag and notifies the user after commit.
- The charge receipt flow appends card lines **only** if the payer already has `card_view_allowed`, so unknown users never receive numbers in that path.

## Low stock alerts

- `LOW_STOCK_THRESHOLD` (default from `.env.example`) controls when unused link count triggers a warning.
- Each `plans` row has `low_stock_rearm`: when stock goes **above** the threshold it becomes `true` (armed). When stock is **at or below** the threshold and the plan is still armed, the bot sends **one** message to active **owner/manager** admins, then sets `low_stock_rearm` to `false` until stock rises above the threshold again.
- Checks run **after successful commit** (separate short session) so alerts never roll back with a failed purchase.

## Operational checklist

- [ ] `.env` not in git; `BOT_TOKEN` rotated if leaked  
- [ ] `OWNER_ID` correct  
- [ ] Database URL uses `+asyncpg` for Postgres  
- [ ] Review audit log table periodically (`audit_logs`)  
- [ ] Confirm only trusted Telegram accounts are promoted to manager  
