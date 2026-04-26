# API panels

## Marzban

- Set panel **base_url** (e.g. `https://panel.example.com`).
- Auth: admin username/password (JWT is obtained automatically) or store encrypted API token in DB.
- Default API prefix is `/api`; override with env **`MARZBAN_API_PREFIX`** if needed.

## Sanaei / 3x-ui

- **base_url** is the web panel URL.
- If the panel uses **web_base_path**, store it in the panel row (the code normalizes slashes).
- **inbound_id** must be set on the panel or server row; without it, user creation fails.

## Manual vs API

API services use `user_services` and stable `/sub/{subscription_token}`. Manual deliveries use `manual_deliveries` and are never returned from `/sub`.
