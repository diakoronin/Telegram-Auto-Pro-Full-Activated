# ربات ارسال لینک تلگرام (پنل وب + زمان‌بندی + چند سرویس همزمان)

این پروژه یک ربات تلگرام است که:

- گروه‌ها را ثبت می‌کند
- لینک‌ها را نگه می‌دارد
- ارسال دستی و زمان‌بندی‌شده انجام می‌دهد
- همزمان چند سرویس ارسال اجرا می‌کند
- فقط به مالک (`OWNER_ID`) دسترسی می‌دهد

---

## امکانات

- ثبت گروه (خودکار و دستی)
- مدیریت لینک‌های سراسری
- انتخاب گروه‌ها برای ارسال دستی
- ساخت سرویس زمان‌بندی (هر سرویس گروه/لینک مستقل)
- اجرای همزمان سرویس‌ها با سقف قابل تنظیم
- پنل وب امن با:
  - مسیر مخفی (`WEB_PANEL_PATH`)
  - لاگین نام کاربری/رمز عبور

---

## تنظیمات مهم `.env`

نمونه:

```env
BOT_TOKEN=توکن_ربات
OWNER_ID=2098876051
STRICT_OWNER_ONLY=true

DB_PATH=bot_data.sqlite3
SEND_DELAY_SECONDS=1.0
SCHEDULER_POLL_SECONDS=5
MAX_CONCURRENT_BROADCASTS=4
SERVICE_NOTIFY_OWNER=true

WEB_PANEL_ENABLED=true
WEB_PANEL_HOST=127.0.0.1
WEB_PANEL_PORT=18080
WEB_PANEL_PATH=mysecretpanel
WEB_PANEL_USERNAME=myadmin
WEB_PANEL_PASSWORD=myStrongPass123
```

### توضیح سریع

- `OWNER_ID`: فقط همین آیدی به بات دسترسی دارد
- `MAX_CONCURRENT_BROADCASTS`: تعداد کار همزمان (مثلا 4)
- `WEB_PANEL_PATH`: مسیر مخفی پنل
- `WEB_PANEL_USERNAME` / `WEB_PANEL_PASSWORD`: لاگین پنل
- پورت پیشنهادی پنل: `18080`

---

## نصب سریع (یک دستور)

> اسکریپت نصب ازت Owner، یوزر/پسورد پنل و مسیر پنل را می‌گیرد و در آخر آدرس پنل را چاپ می‌کند.

```bash
curl -fsSL "https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/cursor/telegram-group-link-bot-6341/install.sh?$(date +%s)" -o /tmp/install.sh && \
sudo BOT_TOKEN='توکن_ربات' OWNER_ID='2098876051' bash /tmp/install.sh
```

---

## اجرای دستی (بدون اسکریپت)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
python3 bot.py
```

---

## دستورات اصلی ربات (فارسی)

- `/start`
- `/help`
- `/whoami`
- `/setlinks`
- `/links`
- `/groups`
- `/addgroup <chat_id> <title>`
- `/removegroup <chat_id>`
- `/refreshadmins`
- `/sendlinks`
- `/services`
- `/runsvc <id>`
- `/enablesvc <id>`
- `/disablesvc <id>`
- `/jobs`
- `/stop`
- `/cancel`
- `/register` (داخل گروه)

---

## وضعیت سرویس

```bash
sudo systemctl status telegram-sender
journalctl -u telegram-sender -f
```
