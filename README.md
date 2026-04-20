# ربات تلگرام مدیریت x-ui (3x-ui)

ربات تلگرامی برای مدیریت پنل 3x-ui — ساخت سرویس VLESS، ارسال لینک به گروه تلگرام، حذف و ریست ترافیک کلاینت‌ها.

---

## امکانات

- **ساخت دسته‌ای ۱ تا ۲۰۰ سرویس VLESS** با یک دستور
- تنظیم **حجم دلخواه** (۰ = نامحدود)
- تنظیم **مدت اعتبار** به روز (۰ = نامحدود / بدون انقضا)
- **ارسال خودکار لینک‌ها** به گروه یا کانال تلگرام
- مشاهده لیست کلاینت‌ها
- حذف کلاینت
- ریست ترافیک
- مشاهده ترافیک مصرفی هر کلاینت
- وضعیت سرور (CPU، RAM، دیسک، آپتایم)

---

## پیش‌نیازها

- Python 3.10+
- پنل **3x-ui** نصب شده روی سرور
- توکن ربات تلگرام از [@BotFather](https://t.me/BotFather)

---

## نصب و راه‌اندازی

```bash
# ۱. کلون پروژه
git clone <repo-url>
cd <repo-folder>

# ۲. نصب وابستگی‌ها
pip install -r requirements.txt

# ۳. تنظیم فایل .env
cp .env.example .env
nano .env   # مقادیر را پر کنید

# ۴. اجرا
python bot.py
```

---

## تنظیمات `.env`

| متغیر | توضیح |
|---|---|
| `BOT_TOKEN` | توکن ربات از @BotFather |
| `ADMIN_IDS` | شناسه عددی ادمین‌ها (با کاما) |
| `XUI_URL` | آدرس پنل x-ui (مثلاً `http://1.2.3.4:54321`) |
| `XUI_USER` | نام کاربری پنل |
| `XUI_PASS` | رمز عبور پنل |
| `XUI_INBOUND_ID` | شناسه اینباند (از تب Inbounds پنل) |
| `SERVER_HOST` | دامنه یا آی‌پی سرور (برای ساخت لینک) |
| `TARGET_CHAT_ID` | شناسه گروه/کانال هدف (`-100...`) |
| `VERIFY_SSL` | بررسی SSL (`true`/`false`) |

---

## دستورات ربات

| دستور | توضیح |
|---|---|
| `/start` | منوی اصلی |
| `/settarget <chat_id>` | تغییر گروه هدف در زمان اجرا |
| `/cancel` | لغو عملیات |
| `/help` | راهنما |

---

## جریان ساخت سرویس

```
/start → منوی اصلی
  ↓ ➕ ساخت سرویس
تعداد (۱-۲۰۰)
  ↓
پیشوند نام (مثلاً: vpn → vpn1, vpn2, ...)
  ↓
حجم GB (۰ = نامحدود)
  ↓
مدت روز (۰ = نامحدود)
  ↓
تایید → ساخت → ارسال لینک‌ها
```

---

## اجرا به عنوان سرویس سیستمی

```ini
# /etc/systemd/system/xui-bot.service
[Unit]
Description=x-ui Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/xui-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
EnvironmentFile=/opt/xui-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable xui-bot
sudo systemctl start xui-bot
```

---

## نکات مهم

- ربات باید **ادمین** گروه/کانال هدف باشد تا بتواند پیام ارسال کند.
- برای پیدا کردن `chat_id` گروه: ربات را به گروه اضافه کنید سپس از دستور `/settarget` استفاده کنید یا از ابزارهایی مثل `@getidsbot` کمک بگیرید.
- اگر پنل x-ui پشت nginx/caddy با SSL قرار دارد، `VERIFY_SSL=true` تنظیم کنید.
