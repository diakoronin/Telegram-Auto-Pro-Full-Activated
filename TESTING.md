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
- [ ] **Shop**: each plan line shows stock count; zero-stock plan cannot be bought (alert).
- [ ] Successful purchase shows server, plan, price, order id, and link; wallet decreases; stock decreases.
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
