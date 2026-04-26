# Manual testing checklist

Run against a staging bot and database. Use a normal user account and separate admin accounts for owner / manager / seller.

## User

- [ ] `/start` shows the branded welcome text and the main inline menu.
- [ ] `/ping` replies that the bot is active.
- [ ] `/help` shows the user guide; admin sees admin guide.
- [ ] **Wallet** shows balance with thousands separators.
- [ ] **Charge**: disabled user sees the “card payment disabled” message.
- [ ] **Charge**: with payment enabled and a public active card, entering a valid amount creates an **invoice** with full card number, holder, bank, amount, invoice id, and expiry note.
- [ ] **Charge**: “Send receipt” then uploading a clear photo attaches receipt; user sees submitted message; admins receive photo with approve/reject.
- [ ] **Charge**: “Cancel invoice” rejects the draft and returns to menu.
- [ ] **Shop**: server list first, then plans for that server only; plan buttons show short `display_name`, price, stock (no server in button).
- [ ] After choosing a plan with stock, bot asks for custom service name (or skip for default); preview then confirm.
- [ ] Successful purchase saves `custom_service_name`; success message shows it; **سرویس‌های من** lists it; detail shows link.
- [ ] **Orders** and **Payments** show history or empty-state messages.

## Admin

- [ ] `/admin` shows grouped menu (sales / users / management).
- [ ] **Sales → Servers**: picking a server shows per-plan unused stock and totals.
- [ ] **Sales → Full stock report** for a server matches SQL counts (unused = `LinkStatus.UNUSED` only).
- [ ] **Sales → All servers** summary lists each server and plan unused counts.
- [ ] **Import links**: paste or `.txt` file; result shows total / added / dup file / dup DB / invalid.
- [ ] **Cards (owner)**: add card stores full number for invoices; list shows public/active flags; toggle public works.
- [ ] **Payment requests**: pending list shows items waiting for receipt review.
- [ ] Approve payment: wallet increases once; second approve fails; user notified.
- [ ] Reject payment: reason asked; user notified with reason.
- [ ] **User card access** grant/revoke still works; granting enables `card_payment_enabled`; revoking disables it.

## Security

- [ ] Non-admin cannot trigger `adm:*` callbacks (unauthorized or no effect).
- [ ] Blocked user cannot charge or buy (blocked message).
- [ ] Duplicate payment approval rejected.
- [ ] Same link not sold twice under concurrency (manual smoke with two purchases).

## Footer / locale

- [ ] With `FOOTER_ENABLED=true` and `TIMEZONE=Asia/Tehran`, user and admin messages end with Jalali date/time footer.

## API panels & stable subscription (needs real panel)

Prereq: PostgreSQL recommended; set `PUBLIC_BASE_URL`, bind subscription port, configure Marzban JSON on `panels` or 3x-ui `servers.inbound_id`.

- [ ] Owner: `/paneladd marzban ...` then `/paneltest PANEL_ID` — **Needs real credentials**
- [ ] Owner: `/serverbind SERVER_ID PANEL_ID [inbound_id]` for 3x-ui
- [ ] User shop: only servers with `panel_id`; purchase creates `user_services` row; success shows stable `/sub/...` URL only (no raw panel URL as “permanent”).
- [ ] `curl http://127.0.0.1:8080/health` returns JSON with database ok/fail.
- [ ] `curl` invalid `/sub/token` → empty/404 safe response.
- [ ] Blocked user: `/sub` returns empty for their token.
- [ ] Traffic sync: logs `traffic_sync` every `TRAFFIC_SYNC_INTERVAL_SECONDS` (watch `logs/bot.log`).
- [ ] Location change: from service details → pick server → same subscription URL; `/sub` returns new configs.
- [ ] Hourly backup: owner receives zip in private chat when `AUTO_BACKUP_ENABLED=true`.
- [ ] `python3 tests/test_quota_calc.py` — local quota math (no network).

## bot-manager.sh

- [ ] Run `./bot-manager.sh` from repo root; menu appears; option 8 curls `/health`.
