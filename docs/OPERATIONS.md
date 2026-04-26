# Operations

## Logs

- `logs/bot.log` — general events  
- `logs/panel_api.log` — panel HTTP (no secrets)  
- `logs/errors.log` — errors  

## Health

`GET http://127.0.0.1:8080/health` — DB connectivity.

## Backups

With PostgreSQL and `AUTO_BACKUP_ENABLED=true`, the bot can send compressed dumps to `OWNER_ID`. Local files live under `backups/` with hourly/daily retention.

## Duplicate process

Two instances with the same `BOT_TOKEN` cause Telegram conflicts. Use `sudo sakabot` → option **25**, then restart the service.
