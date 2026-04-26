# عملیات

## لاگ‌ها

- `logs/bot.log` — رویدادهای عمومی
- `logs/panel_api.log` — درخواست/پاسخ پنل (بدون رمز)
- `logs/errors.log` — خطاها

## Health

`GET http://127.0.0.1:8080/health` — وضعیت اتصال به دیتابیس.

## بکاپ

PostgreSQL + `AUTO_BACKUP_ENABLED` باعث اجرای حلقه بکاپ در پس‌زمینه می‌شود. فایل‌ها در `backups/` با الگوی `hourly_*.sql.gz`.

## تداخل پردازش

اگر دو نمونه با یک `BOT_TOKEN` اجرا شود، تلگرام conflict می‌دهد. از `bot-manager.sh` گزینه‌های ۲۴ و ۲۵ استفاده کنید.
