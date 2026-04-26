# User-facing Persian strings (buttons and messages). ASCII identifiers only.

BLOCKED_USER = "حساب شما مسدود است. در صورت نیاز با پشتیبانی تماس بگیرید."
GENERIC_ERROR = "خطایی رخ داد. لطفاً بعداً دوباره تلاش کنید."
UNAUTHORIZED = "دسترسی مجاز نیست."
CONFIRM_EXPIRED = "درخواست تأیید منقضی شده است. دوباره تلاش کنید."
RATE_LIMIT = "تعداد درخواست‌ها زیاد است. لطفاً کمی صبر کنید."

START_WELCOME = "به ربات فروش خوش آمدید."
MENU_USER = "منوی اصلی:"
MENU_ADMIN = "پنل مدیریت:"

BTN_SHOP = "خرید"
BTN_WALLET = "کیف پول"
BTN_CHARGE = "شارژ حساب"
BTN_HISTORY = "تاریخچه خرید"
BTN_PAYMENT_HISTORY = "درخواست‌های پرداخت"
BTN_SUPPORT = "پشتیبانی"
BTN_CARDS = "شماره کارت‌ها"

CARDS_NOT_ALLOWED = (
    "برای مشاهدهٔ شماره کارت، ابتدا باید توسط پشتیبان تأیید شوید. "
    "با پشتیبانی تماس بگیرید."
)
CARDS_HEADER = "شماره کارت‌های فعال برای واریز:"
CARDS_NONE_ACTIVE = "در حال حاضر کارتی برای نمایش ثبت نشده است."

CARD_ACCESS_GRANTED_USER = "دسترسی شما برای مشاهدهٔ شماره کارت‌ها فعال شد."
CARD_ACCESS_REVOKED_USER = "دسترسی مشاهدهٔ شماره کارت‌های شما برداشته شد."

AMOUNT_PROMPT = "مبلغ شارژ را به تومان وارد کنید (فقط عدد مثبت):"
RECEIPT_PROMPT = "تصویر رسید را ارسال کنید (فقط عکس یا فایل تصویری)."
PENDING_LIMIT = "حداکثر سه درخواست پرداخت در وضعیت انتظار می‌توانید داشته باشید."
RECEIPT_RATE = "حداکثر پنج بار در ساعت می‌توانید رسید ارسال کنید."
INVALID_AMOUNT = "مبلغ نامعتبر است. محدوده مجاز را رعایت کنید."
CHARGE_SUBMITTED = "درخواست شما ثبت شد و پس از بررسی اطلاع داده می‌شود."

NO_PLANS = "در حال حاضر محصولی برای فروش وجود ندارد."
SELECT_PLAN = "پلن را انتخاب کنید:"
INSUFFICIENT_BALANCE = "موجودی کیف پول کافی نیست."
NO_STOCK = "موجودی این پلن تمام شده است."
PURCHASE_OK = "خرید با موفقیت انجام شد. لینک شما:\n{link}"

SUPPORT_SENT = "پیام شما ثبت شد."
SUPPORT_BLOCKED = "امکان ارسال پیام پشتیبانی برای حساب مسدود وجود ندارد."

WALLET_BALANCE = "موجودی فعلی: {balance} تومان"

PAYMENT_APPROVED_USER = "درخواست پرداخت شما تأیید شد و کیف پول شما شارژ شد."
PAYMENT_REJECTED_USER = "درخواست پرداخت شما رد شد. دلیل: {reason}"

CONFIRM_PROMPT = "برای تأیید، دکمه تأیید را بزنید."
ACTION_CANCELLED = "عملیات لغو شد."

VALIDATION_SERVER_NAME = "نام سرور باید بین ۱ تا ۱۲۰ نویسه باشد."
VALIDATION_PLAN_NAME = "نام پلن باید بین ۱ تا ۱۲۰ نویسه باشد."
VALIDATION_PLAN_PRICE = "قیمت پلن باید عدد مثبت باشد."
VALIDATION_CARD = "شماره کارت نامعتبر است (۱۶ رقم)."
VALIDATION_CARD_HOLDER = "نام صاحب کارت باید بین ۱ تا ۱۲۰ نویسه باشد."
VALIDATION_BANK = "نام بانک باید بین ۱ تا ۱۲۰ نویسه باشد."
VALIDATION_LINK = "طول لینک بیش از حد مجاز است."
VALIDATION_CUSTOMER = "طول اطلاعات مشتری بیش از حد مجاز است."
VALIDATION_BULK = "تعداد خطوط بیش از حد مجاز است."
VALIDATION_REASON = "وارد کردن دلیل الزامی است."

IMPORT_RESULT = "اضافه شد: {added} | تکراری نادیده: {dup_batch} | تکراری در دیتابیس: {dup_db}"

BACKUP_OWNER_ONLY = "فقط مالک می‌تواند پشتیبان کامل را دریافت کند."
BACKUP_SENT = "فایل پشتیبان ارسال شد."

REFUND_OK = "بازپرداخت انجام شد."
REFUND_ONCE = "این خرید قبلاً بازپرداخت شده است."

# Card access (admin)
CARD_ACCESS_MENU_TEXT = (
    "تأیید دسترسی مشاهدهٔ شماره کارت برای کاربر:\n"
    "۱) یک پیام از کاربر را به همین ربات فوروارد کنید، یا\n"
    "۲) شناسهٔ عددی تلگرام کاربر را بفرستید.\n"
    "برای لغو از دکمهٔ بازگشت استفاده کنید."
)
CARD_ACCESS_BTN_FORWARD_MODE = "حالت فوروارد"
CARD_ACCESS_BTN_NUMERIC_ID = "شناسهٔ عددی"
CARD_ACCESS_BTN_REVOKE = "لغو دسترسی کاربر"
CARD_ACCESS_BTN_BACK = "بازگشت"
CARD_ACCESS_FWD_INSTRUCTION = (
    "یک پیام از کاربر را به این چت فوروارد کنید (باید فرستنده مشخص باشد؛ "
    "فوروارد ناشناس قابل تأیید نیست)."
)
CARD_ACCESS_ASK_TID = "شناسهٔ عددی تلگرام کاربر را بفرستید:"
CARD_ACCESS_ASK_REVOKE_TID = "شناسهٔ عددی تلگرام کاربری که باید دسترسی کارت‌اش قطع شود:"
CARD_ACCESS_NEED_FORWARD = "لطفاً یک پیام فورواردشده از کاربر بفرستید یا دکمهٔ لغو را بزنید."
CARD_ACCESS_HIDDEN_FORWARD = (
    "فرستندهٔ این فوروارد مشخص نیست. از فوروارد ناشناس استفاده نکنید "
    "یا از گزینهٔ شناسهٔ عددی استفاده کنید."
)
CARD_ACCESS_INVALID_TID = "شناسهٔ نامعتبر است."
CARD_ACCESS_USER_NOT_IN_BOT = "کاربر در ربات ثبت نشده است."
CARD_ACCESS_REVOKE_CONFIRM_EXTRA = "قطع دسترسی مشاهدهٔ کارت برای این کاربر؟"
CARD_ACCESS_BTN_CONFIRM_REVOKE = "تأیید قطع"
CARD_ACCESS_BTN_CANCEL = "لغو"
CARD_ACCESS_USER_BLOCKED = "این کاربر مسدود است؛ ابتدا رفع مسدودیت کنید."
CARD_ACCESS_GRANT_CONFIRM_EXTRA = "فعال‌سازی دسترسی مشاهدهٔ شماره کارت؟"
CARD_ACCESS_BTN_CONFIRM_GRANT = "تأیید دسترسی"
