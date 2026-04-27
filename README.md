# SakaBot — Telegram VPN / service sales bot

Two separate systems: **API** (Marzban / 3x-ui, central quota, stable `/sub/{token}`) and **manual** links (admin delivery, no API quota). **Bot UI strings are Persian**; **installer, logs, and this README are English** for reliable terminals.

---

## One-line install (recommended)

Ubuntu **22.04** or **24.04**, as root or with `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo bash -s --
```

Update only:

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo bash -s -- --update
```

If stdin is the script, the installer reads prompts from **`/dev/tty`**. Do **not** use `sudo bash <(curl …)` (often breaks with `/dev/fd/63`).

Other branch for `git pull` inside the install dir:

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo env INSTALL_BRANCH=mybranch bash -s --
```

Custom install path:

```bash
curl -fsSL https://raw.githubusercontent.com/diakoronin/Telegram-Auto-Pro-Full-Activated/main/install.sh | sudo env INSTALL_DIR=/opt/mybot bash -s --
```

The installer installs system packages, **Python 3.11**, **PostgreSQL** (`sakabot` user/db), asks **BOT_TOKEN**, **OWNER_ID**, **PUBLIC_BASE_URL**, writes **`.env`**, creates **`.venv`**, runs **migrations**, installs **`sakabot`** systemd unit, starts the bot, and symlinks **`sakabot`** to `/usr/local/bin`.

If **`--update`** runs without **`.env`**, the installer will **prompt once** and create `.env` (same questions as first install).

Default **`BRAND_NAME`** in `.env` is **`Sakabot`** (ASCII). Set **`BRAND_NAME=...`** in `.env` to Persian for Telegram welcome text.

Install log: **`/tmp/sakabot-install.log`**

---

## Telegram admin panel

In a private chat with the bot, send **`/admin`** from the account whose numeric Telegram ID equals **`OWNER_ID`** in `.env` (that user is added as **owner** in `admins` on first bot start).

If the bot says you have no access, **`OWNER_ID`** does not match your account — fix it in `.env`, run **`sudo systemctl restart sakabot`**, send **`/start`**, then **`/admin`** again.

### WebApp (Mini App) dashboard

When **`ADMIN_WEBAPP_ENABLED=true`** and **`PUBLIC_BASE_URL`** (or **`WEBAPP_PUBLIC_BASE_URL`**) is **`https://...`**, `/admin` also sends a button **«باز کردن پنل مدیریت»** that opens the glassmorphism admin UI at `/admin-wa/`. The same FastAPI process serves subscription + admin; nginx must proxy HTTPS to port **8080** (see `docs/ADMIN_WEBAPP.md`).

If HTTPS is not configured, the bot only shows the text reply keyboard (fallback).

---

## Manager CLI

```bash
sudo sakabot
```

Or:

```bash
cd /opt/sakabot && sudo ./bot-manager.sh
```

---

## Service & logs

```bash
sudo systemctl status sakabot
sudo journalctl -u sakabot -f
sudo systemctl restart sakabot
```

Health:

```bash
curl -sS http://127.0.0.1:8080/health
```

Subscription URL pattern: `{PUBLIC_BASE_URL}/sub/<token>`

---

## nginx & SSL

After install, you can enable nginx for `/sub/` and `/health` → `127.0.0.1:8080`. For certificates:

```bash
sudo certbot --nginx -d sub.example.com
```

---

## Common errors

### TelegramConflictError

Two processes use the same **BOT_TOKEN**, or a webhook is set. Installer calls **deleteWebhook**. Use **`sudo sakabot`** → **25** to kill duplicates, then restart.

### `.env` / database

Use **`sudo sakabot`** → **19** (masked). Migrations: menu **26**. DB password is **not** printed to the terminal; it lives in `.env`.

---

## Backup / restore

- **`--update`** creates a SQL dump under `/opt/sakabot/backups/` when possible.
- Menu **12** backup, **13** restore.

---

## Advanced manual install

```bash
git clone https://github.com/diakoronin/Telegram-Auto-Pro-Full-Activated.git
cd Telegram-Auto-Pro-Full-Activated
git checkout main
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
# edit .env + PostgreSQL
bash scripts/run_migrations.sh
python3 main.py
```

---

## Developer tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

More: `TESTING.md`, `SECURITY.md`, `docs/API_PANELS.md`, `docs/MANUAL_MODE.md`, `docs/OPERATIONS.md`.
