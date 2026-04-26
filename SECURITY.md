# Security

- Keep `.env` at mode `600` and never commit it to git.
- Do not log `BOT_TOKEN`, panel passwords, 3x-ui cookies, API tokens, or full subscription tokens.
- Panel secrets are stored encrypted with `PANEL_CREDENTIAL_ENCRYPTION_KEY`.
- Full card numbers appear only in user HTML invoices; logs redact cards unless `DEBUG_CARD_LOGGING=true`.
- Invalid `/sub/{token}` requests return empty/404 without leaking whether a token exists.
- Payment approval uses row locking so double approval is not possible.
