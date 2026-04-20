# Telegram Group Link Sender Bot (Web Panel + Scheduler + Multi-Service)

این پروژه یک ربات تلگرام + پنل وب است که دقیقاً برای این سناریو ساخته شده:

- خودت ربات را در گروه‌ها اضافه می‌کنی.
- خودت ربات را ادمین می‌کنی (در صورت نیاز).
- گروه‌ها داخل لیست ثبت می‌شوند.
- لیست لینک می‌دهی.
- هم می‌توانی دستی ارسال کنی، هم سرویس زمان‌بندی‌شده بسازی.
- چند سرویس می‌توانند همزمان اجرا شوند (مثلاً 3 یا 4 سرویس).

> مهم: استفاده از ابزار باید مطابق قوانین تلگرام و قوانین گروه‌ها باشد.

## قابلیت‌ها

- ثبت خودکار گروه و وضعیت ادمین ربات
- ثبت دستی گروه
- مدیریت لینک‌های سراسری
- ارسال دستی با انتخاب گروه‌ها (Inline Keyboard)
- پنل وب برای مدیریت گروه، لینک و سرویس‌ها
- زمان‌بندی ارسال خودکار (service-based scheduler)
- اجرای همزمان چند سرویس با سقف قابل تنظیم
- مالک (Owner) برای کنترل دسترسی دستورات بات

## پیش‌نیاز

- Python 3.10+
- Bot Token از BotFather

## نصب

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## تنظیمات

```bash
cp .env.example .env
```

نمونه:

```env
BOT_TOKEN=123456789:your_telegram_bot_token_here
DB_PATH=bot_data.sqlite3
SEND_DELAY_SECONDS=1.0
MAX_CONCURRENT_BROADCASTS=4
SCHEDULER_POLL_SECONDS=5
SERVICE_NOTIFY_OWNER=true

WEB_PANEL_ENABLED=true
WEB_PANEL_HOST=0.0.0.0
WEB_PANEL_PORT=8080
WEB_PANEL_TOKEN=change_this_long_secret_token
```

### توضیح تنظیمات مهم

- `MAX_CONCURRENT_BROADCASTS`: حداکثر job همزمان (برای نیاز تو روی 3 یا 4 بگذار)
- `SEND_DELAY_SECONDS`: فاصله بین هر پیام
- `WEB_PANEL_TOKEN`: توکن امنیتی پنل وب (حتماً مقدار امن بگذار)

## اجرا

```bash
set -a && source .env && set +a
python3 bot.py
```

با اجرای برنامه:

- بات تلگرام بالا می‌آید.
- پنل وب همزمان روی `WEB_PANEL_HOST:WEB_PANEL_PORT` بالا می‌آید.

اگر `WEB_PANEL_TOKEN` تنظیم کرده باشی، پنل را با این آدرس باز کن:

```text
http://YOUR_HOST:8080/?token=YOUR_TOKEN
```

## راه‌اندازی اولیه در تلگرام

1. به ربات در PV دستور `/start` بده.
2. دستور `/claim` بزن تا Owner ثبت شود.
3. ربات را در گروه‌ها add کن.
4. در هر گروه دستور `/register` بزن (توسط ادمین گروه).
5. اگر لازم بود `/refreshadmins` بزن.
6. لینک‌ها را با `/setlinks` ثبت کن.
7. ارسال دستی:
   - `/sendlinks`
   - گروه‌ها را انتخاب کن
   - `Start send`

## مدیریت از پنل وب

پنل شامل این بخش‌هاست:

- **Groups**: افزودن دستی گروه + مشاهده لیست گروه‌ها
- **Global Links**: ویرایش لینک‌های سراسری (برای `/sendlinks`)
- **Create Scheduled Service**:
  - نام سرویس
  - بازه زمانی (دقیقه)
  - انتخاب گروه‌های هدف
  - لینک‌های اختصاصی سرویس (یا خالی بگذار تا از Global Links استفاده شود)
  - فعال/غیرفعال بودن سرویس
  - اجرای فوری بعد از ساخت
- **Services**:
  - Run now
  - Enable/Disable
  - Delete

## دستورات تلگرام

### Private (PV)

- `/claim`
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
- `/stop` (توقف همه jobهای فعال)
- `/cancel`

### Group

- `/register`

## نکات عملی برای درخواست تو (3-4 سرویس همزمان)

برای اجرای همزمان 3-4 سرویس:

1. در `.env` مقدار زیر را بگذار:
   - `MAX_CONCURRENT_BROADCASTS=4`
2. از پنل وب 3 یا 4 سرویس بساز (هرکدام با گروه/لینک خودش).
3. سرویس‌ها را Enabled نگه دار.
4. زمان‌بند خودکار آن‌ها را بر اساس `interval_minutes` اجرا می‌کند.

## ساختار فایل‌ها

- `bot.py` منطق بات + scheduler + web panel
- `requirements.txt`
- `.env.example`
