# ربات فروش سرویس VPN (تلگرام) — SakaBot

ربات فروش با **دو سیستم جدا**: API (Marzban / 3x-ui) و **دستی** (لینک، بدون سهمیهٔ مرکزی).

---

## نصب با یک دستور (پیشنهادی)

روی **Ubuntu 22.04 یا 24.04** با کاربر root یا sudo.

**پیشنهادی (با `sudo` خطای `/dev/fd/63` نمی‌گیرید):** اسکریپت را از stdin به bash بدهید:

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo bash -s --
```

نصب با آرگومان (مثلاً فقط به‌روزرسانی):

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo bash -s -- --update
```

شاخهٔ دیگر برای `git pull` داخل نصب (متغیر محیطی):

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo env INSTALL_BRANCH=cursor/telegram-vpn-sales-bot-c6f2 bash -s --
```

مسیر نصب سفارشی:

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo env INSTALL_DIR=/opt/mybot bash -s --
```

**جایگزین** اگر به‌صورت root وارد shell شده‌اید (`sudo -i`):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh)
```

**چرا `sudo bash <(curl …)` گاهی خطا می‌دهد؟** فرایند substitution روی کاربر فعلی اجرا می‌شود؛ فایل‌دیسکriptور در subshell ساخته می‌شود و child process `sudo` ممکن است `/dev/fd/N` را نبیند (`No such file or directory`). با `curl … | sudo bash -s --` این مشکل نیست.

نصب‌کننده به‌صورت خودکار انجام می‌دهد: بسته‌های سیستم، Python 3.11، PostgreSQL، کاربر/دیتابیس `sakabot`، پرسش تعاملی (`BOT_TOKEN`, `OWNER_ID`, `PUBLIC_BASE_URL`, …)، ساخت `.env`، venv در `/opt/sakabot/.venv`، `pip install`، migration، سرویس systemd **`sakabot`**، شروع ربات، و دستور **`sakabot`** در `/usr/local/bin`.

اگر پوشهٔ نصب از قبل وجود داشته باشد، منو می‌آید: **به‌روزرسانی** (پیش‌فرض)، نصب مجدد با نگه‌داشتن `.env` و DB، یا نصب کامل با تأیید `DELETE`.

**لاگ نصب:** `/tmp/sakabot-install.log`

---

## به‌روزرسانی یک‌خطی

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo bash -s -- --update
```

یا از منوی مدیریت: `sudo sakabot` → گزینهٔ **۲**.

---

## مدیریت: دستور `sakabot`

```bash
sudo sakabot
```

یا:

```bash
cd /opt/sakabot && sudo ./bot-manager.sh
```

---

## دستورات سرویس و لاگ

```bash
sudo systemctl status sakabot
sudo journalctl -u sakabot -f
sudo systemctl restart sakabot
```

## Health و لینک ساب

```bash
curl -sS http://127.0.0.1:8080/health
```

لینک ساب برای هر سرویس: `{PUBLIC_BASE_URL}/sub/{token}` (بعد از ساخت سرویس در ربات).

---

## nginx و SSL

در پایان نصب، در صورت داشتن آدرس `https` با دامنه، می‌توانید nginx را برای `/sub/` و `/health` به `127.0.0.1:8080` فعال کنید. برای گواهی:

```bash
sudo certbot --nginx -d sub.example.com
```

---

## خطاهای رایج

### TelegramConflictError

دو نمونه با یک `BOT_TOKEN` در حال اجراست، یا webhook فعال است. نصب‌کننده `deleteWebhook` را صدا می‌زند؛ با `sudo sakabot` → **۲۵** پردازش‌های تکراری را حذف کنید و سرویس را ری‌استارت کنید.

### خطای اتصال به PostgreSQL / DATABASE_URL

`.env` را با `sudo sakabot` → **۱۹** (نمایش ماسک‌شده) بررسی کنید. migration: گزینهٔ **۲۶**. رمز DB در ترمینال چاپ نمی‌شود؛ در `.env` ذخیره است.

### ابطال توکن (@BotFather)

اگر توکن عوض شد: `sudo sakabot` → **۱۴** (بدون چاپ توکن در لاگ عمومی).

---

## بکاپ و بازیابی

- هنگام `--update` بکاپ SQL در `/opt/sakabot/backups/` گرفته می‌شود.
- از منو: **۱۲** بکاپ، **۱۳** بازیابی (مسیر فایل).

---

## نصب دستی (پیشرفته)

فقط در صورت نیاز به کنترل کامل دستی:

```bash
git clone https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git
cd Telegram-Auto-Pro-Full-Activated
git checkout main
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
# پر کردن .env و PostgreSQL دستی
bash scripts/run_migrations.sh
python3 main.py
```

---

## تست توسعه‌دهنده

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

جزئیات: `TESTING.md`، امنیت: `SECURITY.md`، پنل‌ها: `docs/API_PANELS.md`، دستی: `docs/MANUAL_MODE.md`، عملیات: `docs/OPERATIONS.md`.
