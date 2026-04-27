/**
 * SakaBot admin WebApp — vanilla JS, RTL
 * API: Authorization: Bearer <initData>
 */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#0a0c10");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#0a0c10");
  }

  const initData = tg && tg.initData ? tg.initData : "";
  const pathPrefix = (document.body.dataset.apiBase || "").replace(/\/$/, "") || "/admin-wa";
  const headers = { "Content-Type": "application/json" };
  if (initData) headers["Authorization"] = "Bearer " + initData;

  const toast = document.getElementById("toast");
  const loading = document.getElementById("loading");
  const denied = document.getElementById("denied");
  const home = document.getElementById("home");
  const subview = document.getElementById("subview");
  const subTitle = document.getElementById("sub-title");
  const subBody = document.getElementById("sub-body");
  const statEls = {
    users: document.getElementById("st-users"),
    subs: document.getElementById("st-subs"),
    sales: document.getElementById("st-sales"),
    servers: document.getElementById("st-servers"),
  };

  const PAGES = {
    buy: "خرید اشتراک",
    "list-sub": "لیست اشتراک‌ها",
    extend: "افزایش زمان / تمدید",
    discount: "کد تخفیف",
    "plans-price": "پلن‌ها و قیمت‌ها",
    stock: "موجودی سرورها",
    "user-list": "لیست کاربران",
    "user-search": "جستجوی کاربر",
    payments: "پرداخت‌ها",
    "charge-manual": "شارژ دستی",
    block: "مسدود / فعال‌سازی",
    broadcast: "پیام همگانی",
    "r-sales": "گزارش فروش",
    "r-users": "گزارش کاربران",
    "r-subs": "گزارش اشتراک‌ها",
    "manage-servers": "مدیریت سرورها",
    "bot-settings": "تنظیمات ربات",
    admins: "مدیریت ادمین‌ها",
    "test-conn": "تست اتصال",
    "sys-status": "وضعیت سیستم",
    logs: "لاگ‌ها",
    backup: "بکاپ دیتابیس",
    "cache-reload": "ریلود کش",
    "bot-info": "اطلاعات ربات",
    "st-general": "تنظیمات عمومی",
    "st-brand": "نام برند",
    "st-banners": "بنرها / متن‌ها",
    "st-pay": "تنظیمات پرداخت",
    "st-notif": "تنظیمات نوتیفیکیشن",
    "st-panel": "تنظیمات پنل / Provider",
  };

  function showToast(msg, isErr) {
    if (!toast) return;
    toast.textContent = msg;
    toast.className = "";
    toast.classList.add(isErr ? "error" : "ok", "show");
    setTimeout(function () {
      toast.classList.remove("show");
    }, 3200);
  }

  async function apiGet(path) {
    const r = await fetch(pathPrefix + path, { headers: headers, credentials: "same-origin" });
    if (r.status === 401) throw new Error("لطفاً از داخل تلگرام باز کنید.");
    if (r.status === 403) throw new Error("فقط ادمین.");
    if (!r.ok) throw new Error("خطا: " + r.status);
    return r.json();
  }

  async function loadSummary() {
    if (!initData) return;
    if (loading) loading.classList.add("active");
    try {
      const d = await apiGet("/api/summary");
      if (statEls.users) statEls.users.textContent = d.total_users != null ? d.total_users : "—";
      if (statEls.subs) statEls.subs.textContent = d.active_subscriptions != null ? d.active_subscriptions : "—";
      if (statEls.sales) statEls.sales.textContent = d.today_completed_sales != null ? d.today_completed_sales : "—";
      if (statEls.servers) statEls.servers.textContent = d.active_servers != null ? d.active_servers : "—";
    } catch (e) {
      showToast(e.message || String(e), true);
    } finally {
      if (loading) loading.classList.remove("active");
    }
  }

  async function stubAction(action) {
    showToast("…", false);
    try {
      const r = await fetch(pathPrefix + "/api/placeholder/" + encodeURIComponent(action), {
        method: "POST",
        headers: headers,
        body: "{}",
      });
      if (!r.ok) throw new Error("خطا " + r.status);
      const j = await r.json();
      showToast(j.message || "ثبت شد (نمونه)", false);
    } catch (e) {
      showToast(e.message || String(e), true);
    }
  }

  function goHome() {
    if (subview) subview.classList.remove("active");
    if (home) home.classList.remove("hidden");
  }

  function openSub(sec) {
    if (!initData) {
      showToast("مینی‌اپ باید از داخل تلگرام باز شود.", true);
      return;
    }
    const title = PAGES[sec] || sec;
    if (subTitle) subTitle.textContent = title;
    if (subBody)
      subBody.innerHTML =
        "<p>این بخش رابط کامل دارد اما اجرای عملیات فعال می‌شود در نسخه بعد. می‌توانید هم‌زمان از <strong>دکمه‌های مدیریت</strong> در ربات تلگرام استفاده کنید.</p>" +
        "<p class=\"empty\" style=\"margin-top:10px;\"><button type=\"button\" class=\"card-btn\" id=\"sub-stub\" data-stub=\"" +
        sec +
        "\">ارسال درخواست (نمونه API)</button></p>";
    const b = document.getElementById("sub-stub");
    if (b) b.addEventListener("click", function () { stubAction(sec); });
    if (subview) subview.classList.add("active");
    if (home) home.classList.add("hidden");
  }

  const btnRefresh = document.getElementById("btn-refresh");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", function () {
      loadSummary();
      showToast("بروزرسانی", false);
    });
  }
  const btnHome = document.getElementById("btn-home");
  if (btnHome) btnHome.addEventListener("click", goHome);

  document.querySelectorAll("[data-sec]").forEach(function (el) {
    el.addEventListener("click", function () {
      openSub(el.getAttribute("data-sec"));
    });
  });

  function boot() {
    if (!initData) {
      if (denied) {
        denied.style.display = "block";
        denied.classList.add("active");
      }
      if (home) home.classList.add("hidden");
      if (loading) {
        loading.classList.remove("active");
        loading.style.display = "none";
      }
      return;
    }
    if (denied) {
      denied.style.display = "none";
      denied.classList.remove("active");
    }
    if (home) {
      home.classList.remove("hidden");
      home.classList.add("active");
    }
    if (loading) {
      loading.classList.remove("active");
      loading.style.display = "none";
    }
    loadSummary();
  }

  boot();
})();
