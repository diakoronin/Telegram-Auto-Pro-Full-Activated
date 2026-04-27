# Admin Telegram WebApp (Mini App)

## What it is

- Dark glassmorphism UI at `{HTTPS_ORIGIN}/admin-wa/`
- Served by the **same** FastAPI app as `/sub` and `/health` (port `SUBSCRIPTION_API_PORT`, default 8080)
- API routes under `/admin-wa/api/*` require `Authorization: Bearer <Telegram.WebApp.initData>` and **HMAC verification** (bot token on server)
- Only users in `admins` table (or `OWNER_ID`) are allowed

## Environment

| Variable | Description |
|----------|-------------|
| `ADMIN_WEBAPP_ENABLED` | `true` to mount the WebApp (default `true`) |
| `WEBAPP_PUBLIC_BASE_URL` | Optional HTTPS origin if different from `PUBLIC_BASE_URL` (no trailing path) |
| `PUBLIC_BASE_URL` | Used for WebApp URL if `WEBAPP_PUBLIC_BASE_URL` is empty. Must be `https://...` in production |

Full Mini App URL for BotFather: **`{PUBLIC_BASE_URL or WEBAPP_PUBLIC_BASE_URL}/admin-wa/`**

## nginx

Proxy the same host used for subscription:

```nginx
location /admin-wa/ {
    proxy_pass http://127.0.0.1:8080/admin-wa/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

SSL: required for `WebApp` button in Telegram (HTTPS only).

## BotFather

1. @BotFather → your bot → **Bot Settings** → **Menu Button** (optional) or use inline WebApp from `/admin` as implemented.
2. If you set a default Mini App URL: use `https://your-domain.com/admin-wa/` (must match your nginx + SSL).

## Fallback

- `/admin` still shows reply keyboard (Persian) if WebApp is disabled, HTTPS is missing, or the user is not on Telegram.
