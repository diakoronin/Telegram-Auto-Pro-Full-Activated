# ربات تلگرامی مدیریت x-ui / 3x-ui

یک ربات تلگرام ساده برای اتصال به پنل **x-ui / 3x-ui** که با آن می‌توانید:

- 📋 لیست اینباندها را ببینید.
- ➕ سرویس **تکی** بسازید.
- 🧰 به‌صورت **انبوه ۱ تا ۲۰۰ سرویس** روی یک اینباند بسازید.
- 📤 لینک‌های ساخته‌شده را به **یک گروه / کانال تلگرامی** دلخواه بفرستید.
- ⏳ **حجم** (به GB) و **مدت** (به روز) را خودتان تعیین کنید. وارد کردن `0` یعنی **نامحدود** (پیش‌فرض برای مدت، چون معمولاً نامحدود می‌زنید).

> ✅ سازگار با پنل‌های `MHSanaei/3x-ui` و `alireza0/x-ui`.

---

## 🚀 نصب سریع

پیش‌نیازها: **Python 3.10+**

```bash
git clone <this-repo-url>
cd <repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # مقادیر را پر کنید
```

## 🔑 تنظیم متغیرهای محیطی

فایل `.env` را بر اساس نمونه پر کنید:

| متغیر | توضیح |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | توکن ربات از `@BotFather` |
| `TELEGRAM_ADMIN_IDS` | آی‌دی عددی ادمین‌ها (با کاما جدا کنید). فقط این افراد می‌توانند از ربات استفاده کنند. از `@userinfobot` آی‌دی خود را بگیرید. |
| `XUI_BASE_URL` | آدرس کامل پنل مثل `https://panel.example.com:2053` |
| `XUI_USERNAME` | نام کاربری پنل |
| `XUI_PASSWORD` | رمز عبور پنل |
| `XUI_WEB_BASE_PATH` | اگر هنگام نصب x-ui برای پنل یک `webBasePath` گذاشته‌اید، مثل `/mypath/` اینجا بگذارید. اگر ندارید، مقدار `/` کافی است. |
| `XUI_INSECURE_TLS` | اگر پنل روی `https` با گواهی self-signed هست، `true` بگذارید. |
| `DEFAULT_SEND_CHAT_ID` | (اختیاری) پیش‌فرضِ گروه/کانالی که لینک‌ها را به آنجا می‌فرستید. مثال: `-1001234567890` یا `@my_channel`. |
| `DB_PATH` | مسیر دیتابیس SQLite داخلی برای لاگ و تاریخچه Jobها. |
| `LOG_LEVEL` | پیش‌فرض `INFO`. برای عیب‌یابی `DEBUG` بگذارید. |

> ⚠️ **نکتهٔ مهم**: ربات باید در گروه یا کانال مقصد **ادمین** باشد تا بتواند پیام بفرستد.

## ▶️ اجرا

```bash
python -m xui_bot.bot
```

برای اجرای دائم روی سرور با `systemd`:

```ini
# /etc/systemd/system/xui-bot.service
[Unit]
Description=x-ui Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xui-bot
EnvironmentFile=/opt/xui-bot/.env
ExecStart=/opt/xui-bot/.venv/bin/python -m xui_bot.bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xui-bot
sudo journalctl -u xui-bot -f
```

## 🧑‍💻 استفاده در تلگرام

دستورات اصلی:

| دستور | کار |
| --- | --- |
| `/start` | منوی اصلی |
| `/inbounds` | فهرست اینباندها |
| `/new` | ساخت **تکی** یک سرویس |
| `/bulk` | ساخت **انبوه ۱ تا ۲۰۰ سرویس** |
| `/jobs` | تاریخچهٔ ده Job اخیر |
| `/cancel` | لغو گفت‌وگوی فعلی |

### فلوی ساخت انبوه

1. اینباند را انتخاب کنید.
2. **تعداد** سرویس (عدد بین `1` تا `200`).
3. **حجم** هر سرویس به GB (`0` = نامحدود).
4. **مدت** به روز (`0` = نامحدود). ⬅ چون شما معمولاً نامحدود می‌زنید، همین `0` را بفرستید.
5. **پیشوند** برای نام کاربر (مثل `vip`). برای رد شدن: `-`.
6. **چت مقصد** ارسال لینک‌ها:
   - آی‌دی گروه/کانال (مثل `-1001234567890`)
   - یا یوزرنیم (مثل `@my_ch`)
   - یا `here` = در همین چت
   - یا `default` = از مقدار `DEFAULT_SEND_CHAT_ID`
   - یا `skip` = فقط برای خودم، به جای خاصی نفرست.
7. تأیید نهایی → ساخت انجام و لینک‌ها در بسته‌های زیر ۴۰۰۰ کاراکتر ارسال می‌شوند (هر لینک در یک بلاک `<code>` قابل کپی).

## 🔒 امنیت

- فقط ادمین‌های معرفی‌شده در `TELEGRAM_ADMIN_IDS` به ربات دسترسی دارند.
- هیچ خروجی‌ای از دیتابیس کاربران پنل به بیرون ارسال نمی‌شود مگر لینک‌های اشتراکی که شما درخواست می‌دهید.
- توکن ربات و رمز پنل فقط در `.env` نگهداری می‌شوند؛ آن را در ریپو commit **نکنید**.

## 🛠 ساختار پروژه

```
xui_bot/
├── __init__.py
├── bot.py            # ورودی اصلی + Handler ها + Conversation
├── config.py         # بارگذاری .env
├── store.py          # SQLite برای Jobها
├── xui_client.py     # کلاینت async پنل x-ui
├── link_builder.py   # ساخت URI برای vless/vmess/trojan/ss
└── utils.py          # توابع کمکی (حجم، تاریخ، …)
```

## 🧩 پروتکل‌های پشتیبانی‌شده

- `VLESS` (TCP / WS / gRPC / Reality / TLS)
- `VMess`
- `Trojan`
- `Shadowsocks`

## ❓ عیب‌یابی

- **`Login failed`** → یوزر/پسورد یا `XUI_WEB_BASE_PATH` اشتباه است.
- **`non-JSON`** → احتمالاً پنل پشت CDN/Cloudflare بدون cookie pass-through است. یا `XUI_WEB_BASE_PATH` اشتباه است.
- **لینک به گروه ارسال نمی‌شود** → ربات باید در گروه/کانال **ادمین** باشد و «ارسال پیام» روشن باشد.
- **خطای TLS self-signed** → `XUI_INSECURE_TLS=true`.

## 📜 License

MIT
