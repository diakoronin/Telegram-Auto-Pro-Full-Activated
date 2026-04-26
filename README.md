# ربات فروش سرویس VPN (تلگرام)

ربات فروش حرفه‌ای با **دو سیسته جدا**:

1. **سیستم اصلی API** — اتصال به پنل Marzban و Sanaei/3x-ui، کنترل مرکزی حجم، لینک اشتراک ثابت روی دامنه شما (`/sub/{token}`).
2. **سیستم دستی** — ایمپورت لینک، تحویل توسط ادمین، بدون همگام‌سازی حجم با API.

## پیش‌نیازها

- Python 3.11+
- PostgreSQL (پیشنهادی برای production)
- توکن ربات، شناسه عددی مالک، کلید رمزنگاری پنل

## نصب سریع

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
# مقادیر را پر کنید
python3 main.py
```

اجرای تست‌ها:

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

## systemd

فایل نمونه: `deploy/telegram-vpn-bot.service` — مسیر `/opt/sakabot` و کاربر `sakabot` را با محیط خود تطبیق دهید.

```bash
sudo cp deploy/telegram-vpn-bot.service /etc/systemd/system/telegram-vpn-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-vpn-bot
journalctl -u telegram-vpn-bot -f
```

## پایگاه داده و migration

هنگام بالا آمدن برنامه، migration به‌صورت خودکار اجرا می‌شود. برای اجرای دستی از `bot-manager.sh` گزینه ۲۶ استفاده کنید.

## nginx و SSL

- API اشتراک روی `127.0.0.1:8080` (قابل تنظیم با `SUBSCRIPTION_API_HOST` / `SUBSCRIPTION_API_PORT`).
- nginx باید `https://your-domain/sub/` را به همان پورت پروکسی کند (نمونه در `bot-manager.sh` گزینه ۳۳).
- SSL: `certbot --nginx -d your-domain.com`

## TelegramConflictError

اگر همزمان webhook و polling فعال باشد، خطای conflict رخ می‌دهد. برنامه هنگام شروع `delete_webhook(drop_pending_updates=True)` را صدا می‌زند. مطمئن شوید جای دیگری با همین توکن polling نمی‌زند.

## بکاپ

با `AUTO_BACKUP_ENABLED=true` و دیتابیس PostgreSQL، بکاپ فشرده ساخته می‌شود و در صورت اندازه مناسب به چت خصوصی `OWNER_ID` ارسال می‌گردد؛ نگهداری محلی در پوشه `backups/` با سیاست نگهداری ساعتی/روزانه.

## bot-manager

اسکریپت تعاملی VPS:

```bash
sudo ./bot-manager.sh
```

## پنل Marzban و 3x-ui

جزئیات اتصال و نکات امنیتی در `docs/API_PANELS.md`.

## حالت دستی

راهنمای ایمپورت TXT و تحویل در `docs/MANUAL_MODE.md`.

## عملیات و عیب‌یابی

`docs/OPERATIONS.md`
