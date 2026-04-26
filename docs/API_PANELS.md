# API panels (Marzban & 3x-ui)

## Marzban

- Auth: `POST /api/admin/token` with form `username`, `password`, or set `api_token_encrypted` on the panel row.
- Create user: `POST /api/user` (see Marzban `UserCreate` schema).
- **Required in DB** for automated user creation: columns `panels.marzban_proxies_json` and `panels.marzban_inbounds_json` (JSON objects matching Marzban `proxies` and `inbounds`).

Owner can set JSON via SQL or future admin UI. Example shape:

```json
{"vless": {}}
```

```json
{"vless": ["VLESS TCP REALITY"]}
```

(Use tags that exist on your Marzban instance.)

## 3x-ui (MHSanaei)

- Auth: `POST {base}/login` JSON body; session cookie stored in-memory per panel process.
- API base: `{base}/panel/api/inbounds/...`
- **Required**: `servers.inbound_id` for the inbound to attach clients.

## Owner commands (quick setup)

- `/paneladd marzban Name https://panel.url admin password`
- `/paneladd 3xui Name https://panel.url admin password`
- `/paneltest PANEL_ID`
- `/serverbind SERVER_ID PANEL_ID [inbound_id]`

## User purchase

Shop only lists servers with `panel_id` set and plans with `is_visible_to_users`. Purchase calls the provider and creates `user_services` + `panel_accounts`.
