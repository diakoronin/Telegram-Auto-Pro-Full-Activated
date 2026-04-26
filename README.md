# ![Locations](https://github.com/M4nifest0/M4nifest0_WhatsApp/blob/master/s.png) 

# Telegram-Auto-Pro-Full-Activated

## GitHub

Repository: https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated

## ربات تلگرام گروه و لینک ساب (`group_sub_bot.py`)

کد ربات روی شاخه **`cursor/telegram-group-sub-bot-0f07`** است (تا وقتی به `main` مرج شود). Python 3.9+ لازم است.

### نصب مستقیم روی سرور

```bash
git clone https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git
cd Telegram-Auto-Pro-Full-Activated
git checkout cursor/telegram-group-sub-bot-0f07
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

سپس در `.env` مقداردهی کنید: `TELEGRAM_BOT_TOKEN` و `TELEGRAM_ADMIN_IDS` (آیدی عددی تلگرام، با کاما برای چند نفر). اجرا:

```bash
source .venv/bin/activate
set -a && source .env && set +a
python3 group_sub_bot.py
```

اگر ترجیح می‌دهید بدون فایل `.env` باشد:

```bash
export TELEGRAM_BOT_TOKEN="توکن_از_BotFather"
export TELEGRAM_ADMIN_IDS="123456789"
python3 group_sub_bot.py
```

### آپدیت مستقیم روی سرور

```bash
cd Telegram-Auto-Pro-Full-Activated
git fetch origin
git checkout cursor/telegram-group-sub-bot-0f07
git pull origin cursor/telegram-group-sub-bot-0f07
source .venv/bin/activate
pip install -r requirements.txt
```

بعد ربات را دوباره اجرا کنید (مثلاً `systemctl restart ...` یا `Ctrl+C` و دوباره `python3 group_sub_bot.py`).

**بعد از مرج به `main`:** به‌جای شاخهٔ بالا از `git checkout main` و `git pull origin main` استفاده کنید.

**همگام‌سازی لیست گروه با تلگرام:** با `/listgroups`، `/mygroups`، `/pick` و `/admincheck` قبل از نمایش، عنوان گروه‌ها از API خوانده می‌شود و اگر ربات از گروه بیرون انداخته شده باشد آن رکورد حذف می‌شود. رویداد خروج ربات (`my_chat_member`) و تغییر نام گروه (`new_chat_title`) هم ذخیره را به‌روز می‌کنند. برای همگام‌سازی دستی: `/syncgroups`.

### ربات «تغییری نکرده» — کش یا سرور؟

تلگرام **کد پایتون شما را کش نمی‌کند**؛ هر درخواست به API همان لحظه پردازش می‌شود. اگر رفتار عوض نشده، معمولاً یکی از این‌هاست:

1. **روی سرور هنوز نسخهٔ قدیم اجرا می‌شود** (فراموش کردن `git pull`، مسیر اشتباه، یا ری‌استارت نکردن `systemd`/فرایند).
2. **ربات دیگری** با همان نام/توکن دیگر اجرا می‌شود و شما به همان پیام می‌دهید.
3. **کد دیگری** اجرا می‌شود (مثلاً پروژهٔ «ادمین ساکانت» جدا از این مخزن).

**بررسی از داخل تلگرام:** بعد از آپدیت سرور، به ربات بزنید **`/version`**. باید نسخهٔ فعلی (مثلاً `1.3.0`)، مسیر فایل `group_sub_bot.py` روی سرور، و زمان آخرین تغییر فایل را ببینید. اگر `/version` وجود ندارد یا نسخه قدیمی است، سرور را درست آپدیت/ری‌استارت نکرده‌اید.

**بررسی روی سرور (SSH):**

```bash
cd /مسیر/پروژه/Telegram-Auto-Pro-Full-Activated
git branch --show-current
git log -1 --oneline
grep BOT_VERSION group_sub_bot.py
ps aux | grep group_sub_bot
```

باید شاخهٔ درست، آخرین کامیت، و **یک** فرایند برای همان مسیر ببینید. اختیاری: قبل از اجرا `export GROUP_SUB_BOT_BUILD="$(date -u +%Y%m%d-%H%M)"` بگذارید تا در `/version` هم دیده شود.

##### Program Features
----------------------
📌 activated

📌 No proxy required

📌 Relatively good speed

📌 Requires a virtual server

📌 Slowly adds a member to the group and is a little slow

📌 The full version is hassle-free and fully active

📌 This application is completely free

# Disclaimer:
----------------------
- 📌 This tool is designed and developed for professionals and researchers. So do not target others and do not test them for no reason :)

# See how it works:
----------------------
- 🔞 https://youtu.be/StG17vQf64E

# PassWord File:
----------------------
- 🔞 hack4lx.py

# Link Download File:
----------------------
- 🔞 https://m4nifest0.com/product/telegram-auto-pro-full-activated/

- 🔞 https://m4nifest0.shop/product/telegram-auto-pro-full-activated/

- 🔞 https://m4nifest0.group/product/telegram-auto-pro-full-activated/

# How to ger:
----------------------
- 📌 Visit our channel or our site to download .

- 🔞 https://m4nifest0.com
- 🔞 https://m4nifest0.group
- 🔞 https://m4nifest0.shop
- 🔞 https://t.me/M4nifest0

----------------------

<h2>- 📌 Get the tool via the links below</h2>
<p align="center">	
</a>&nbsp;&nbsp;&nbsp;&nbsp;
	<a href="https://t.me/M4nifest0">
		<img src="https://img.shields.io/badge/Telegram-%23000000.svg?&style=for-the-badge&logo=Telegram&logoColor=white" />
	</a>&nbsp;&nbsp;&nbsp;&nbsp;
	<a href="https://www.instagram.com/_m4nifest0_/">
		<img src="https://img.shields.io/badge/instagram-%23E4405F.svg?&style=for-the-badge&logo=instagram&logoColor=white" />
	</a>&nbsp;&nbsp;&nbsp;&nbsp;
	<a href="https://www.youtube.com/c/cybermonitoringhack4lx">
		<img src="https://img.shields.io/badge/youtube-%23FF0000.svg?&style=for-the-badge&logo=youtube&logoColor=white" />
	</a>&nbsp;&nbsp;&nbsp;&nbsp;
	<a href="https://twitter.com/_M4nifest0_">
		<img src="https://img.shields.io/badge/twitter-%231DA1F2.svg?&style=for-the-badge&logo=twitter&logoColor=white" />
	</a>&nbsp;&nbsp;&nbsp;&nbsp;
	<a href="https://m4nifest0.com">
		<img src="https://img.shields.io/badge/WebSite-%234A154B.svg?&style=for-the-badge&logo=slack&logoColor=white" />
	</a>&nbsp;&nbsp;&nbsp;&nbsp;
</p>

<h2>📌 Our team specializes in the following programming languages:...</h2> 
<p align="center">	
	<img src="https://img.shields.io/badge/node.js%20-%2343853D.svg?&style=for-the-badge&logo=node.js&logoColor=white" />
        <img src="https://img.shields.io/badge/python%20-%2314354C.svg?&style=for-the-badge&logo=python&logoColor=white" />
	<img src="https://img.shields.io/badge/c%23%20-%23239120.svg?&style=for-the-badge&logo=c-sharp&logoColor=white" />
	<img src="https://img.shields.io/badge/java-%23ED8B00.svg?&style=for-the-badge&logo=java&logoColor=white" />
	<img src="https://img.shields.io/badge/php-%23777BB4.svg?&style=for-the-badge&logo=php&logoColor=white" />
	<img src="https://img.shields.io/badge/ruby-%23CC342D.svg?&style=for-the-badge&logo=ruby&logoColor=white" />
	<img src="https://img.shields.io/badge/perl-%2339457E.svg?&style=for-the-badge&logo=perl&logoColor=white" />
	<img src="https://img.shields.io/badge/c++%20-%2300599C.svg?&style=for-the-badge&logo=c%2B%2B&logoColor=white" />
</p>
