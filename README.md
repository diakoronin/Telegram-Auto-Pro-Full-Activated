# Telegram X-UI Manager Bot

ربات تلگرامی برای مدیریت سرویس‌های X-UI (3x-ui) از داخل تلگرام.

## قابلیت‌ها

- اتصال مستقیم به API پنل X-UI
- نمایش لیست inbound ها
- ساخت دسته‌ای سرویس از `1` تا `200` عدد در هر درخواست
- امکان تعیین حجم و زمان دلخواه
  - `0` برای حجم = نامحدود
  - `0` برای زمان = نامحدود
- تعیین پیشوند نام کاربر و `start_index` (مثلا از `0` شروع شود)
- ارسال خودکار خروجی سرویس‌ها به گروه تلگرام
- محدود کردن دسترسی به Admin ID

## نصب

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

مقادیر فایل `.env` را با اطلاعات واقعی خود پر کنید.

## تنظیمات (`.env`)

```env
BOT_TOKEN=123456:telegram-bot-token
XUI_BASE_URL=http://127.0.0.1:2053
XUI_USERNAME=admin
XUI_PASSWORD=admin
ADMIN_IDS=123456789
DEFAULT_GROUP_CHAT_ID=-1001234567890
REQUEST_TIMEOUT_SECONDS=25
STATE_FILE=bot_state.json
```

- `ADMIN_IDS`: لیست آی‌دی ادمین‌ها به صورت کاما جدا
- `DEFAULT_GROUP_CHAT_ID`: اگر بگذارید، خروجی پیش‌فرض به این گروه ارسال می‌شود
- با دستور `/setgroup` می‌توانید گروه مقصد را در زمان اجرا تغییر دهید
- `XUI_BASE_URL` می‌تواند هم آدرس ریشه باشد (`http://ip:2053`) و هم آدرس کامل پنل
  (مثل `http://ip:2053/random/panel/inbounds`)؛ ربات خودش normalize می‌کند.

## اجرا

```bash
python3 bot.py
```

## دستورات ربات

- `/start` نمایش راهنما
- `/help` نمایش فرمت ساخت سرویس
- `/health` بررسی اتصال به X-UI
- `/inbounds` لیست inbound ها
- `/setgroup <chat_id>` تنظیم گروه مقصد
- `/group` نمایش گروه مقصد فعلی
- `/create <inbound_id> <count> <volume_gb> <days> [prefix] [start_index]`

## نمونه ساخت سرویس

```text
/create 3 10 50 30 user 0
```

- 10 سرویس روی inbound شماره 3
- حجم هر سرویس 50 گیگ
- زمان هر سرویس 30 روز
- نام‌ها از `user0` شروع می‌شوند

برای نامحدود:

```text
/create 3 20 0 0 vip 0
```

## نکات

- برای ارسال پیام در گروه، ربات باید در گروه عضو باشد.
- `chat_id` گروه معمولا با `-100` شروع می‌شود.
- لینک خروجی بر اساس نوع پروتکل inbound ساخته می‌شود (vmess / vless / trojan / ss).
