"""Button labels and menu titles for the Telegram chat admin panel (Persian)."""

# Root (after opening admin)
# Use ASCII-only prefix so Telegram never strips/normalizes a different "plus" than in code
BTN_ADD_PANEL = "افزودن پنل"
# What clients / old keyboards may still send (F.text must match)
BTN_ADD_PANEL_ALIASES: tuple[str, ...] = (
    "افزودن پنل",
    "+ افزودن پنل",
    "+  افزودن پنل",  # double space (some clients)
    "➕ افزودن پنل",  # legacy
    "➕افزودن پنل",
)
BTN_USER_MGMT = "👥 مدیریت کاربران"
# Some Telegram clients use single-person bust (👤) instead of busts in silhouette (👥)
BTN_USER_MGMT_ALIASES: tuple[str, ...] = (
    "👥 مدیریت کاربران",
    "👤 مدیریت کاربران",
    "👤مدیریت کاربران",
)
BTN_FINANCE = "💰 مالی / کارت به کارت"
BTN_EDUCATION = "📚 بخش آموزش"
BTN_SALES = "📦 فروش و موجودی"
BTN_REPORTS = "📊 گزارش‌ها"
BTN_SYSTEM = "⚙️ سیستم"
# Must differ from user main `🏠 منوی اصلی` or non-admins' home button breaks when admin router is first
BTN_BACK_MAIN = "⬅️ منوی اصلی کاربری"
BTN_BACK_ADMIN = "⬅️ پنل مدیریت"

# Add panel (plain labels — no fancy arrows; Telegram may normalize Unicode and break F.text==)
BTN_TYPE_MARZBAN = "Marzban (مزربن)"
BTN_TYPE_3XUI = "3x-ui / Sanaei"

# Users
BTN_USER_SEARCH = "🔎 جستجوی کاربر"
BTN_USER_LIST = "📋 فهرست کاربران (صفحه‌بندی)"
BTN_USER_BULK_CREDIT = "💳 شارژ گروهی"
BTN_USER_BROADCAST = "📢 ارسال پیام گروهی"

# Finance
BTN_CARDS = "💳 مدیریت کارت‌های بانکی"
BTN_C2C_TEXT = "📝 راهنمای کارت‌به‌کارت (متن نمایش)"
BTN_PENDING_PAY = "⏳ درخواست‌های پرداخت در انتظار"
BTN_ADD_CARD = "➕ ثبت کارت بانکی جدید"

# Education
BTN_EDU_ADD = "➕ افزودن محتوا"
BTN_EDU_EDIT = "📝 ویرایش محتوا"
BTN_EDU_DEL = "🗑 حذف محتوا"
BTN_EDU_LIST = "📋 فهرست محتوا"

# Sales / stock
BTN_LIST_PANELS = "🌐 فهرست پنل‌های API"
BTN_IMPORT = "📥 ایمپورت لینک TXT"
BTN_DELIVER = "🛒 تحویل دستی"
BTN_LINK_STOCK = "📊 موجودی لینک دستی"

# System
BTN_SYNC = "🔄 اجرای سینک حالا"
BTN_DB_HEALTH = "🩺 وضعیت دیتابیس"

# Cancel FSM
BTN_CANCEL = "❌ انصراف"
BTN_CONFIRM = "✅ تایید"
BTN_PAGE_PREV = "⬅️ صفحه قبل"
BTN_PAGE_NEXT = "صفحه بعد ➡️"

# SSL (short labels for reply keyboard)
BTN_SSL_ON = "✅ SSL: روشن"
BTN_SSL_OFF = "⛔ SSL: خاموش"

ADMIN_MENU_TITLE = "پنل مدیریت (چت) — منوی اصلی"
