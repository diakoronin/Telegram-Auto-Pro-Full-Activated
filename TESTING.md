# تست

## اجرا

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

## پوشش

تست‌های mock بدون پنل واقعی شامل: saga خرید API، خرید 3x-ui در صورت نبود کانفیگ، migration لوکیشن، API اشتراک، فاکتور کارت، کیف پول، ایمپورت/تحویل دستی، محاسبه سهمیه، سینک ترافیک، گزارش CSV ساده، تیکت.

## تست زنده پنل

**Needs real credentials** — برای Marzban و 3x-ui باید `DATABASE_URL`، پنل واقعی و توکن/رمز معتبر تنظیم شود؛ این موارد در CI اجرا نمی‌شوند.

## نصب روی سرور (بدون pytest)

برای تست نصب واقعی از GitHub از `README.md` و اسکریپت `install.sh` استفاده کنید (Ubuntu 22.04/24.04، نیاز به `sudo`).
