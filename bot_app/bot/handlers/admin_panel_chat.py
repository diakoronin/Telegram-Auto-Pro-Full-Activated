"""Chat admin: structured menus, FSM. Register before `admin_handlers`."""

from __future__ import annotations

import html
import re
import uuid
from datetime import datetime, timezone
from typing import List, Sequence

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy import and_, desc, func, select, text

import bot_app.bot.admin_texts as T
from bot_app.bot.handlers.common import get_admin, is_owner_or_manager
from bot_app.bot.keyboards import (
    admin_add_panel_kb,
    admin_cancel_row_kb,
    admin_education_menu_kb,
    admin_finance_menu_kb,
    admin_root_menu_kb,
    admin_sales_menu_kb,
    admin_system_menu_kb,
    admin_user_list_nav_kb,
    admin_user_menu_kb,
    main_user_kb,
)
from bot_app.bot.states import (
    AddCardStates,
    AddPanelStates,
    BroadcastStates,
    BulkCreditStates,
    C2CTextStates,
    EduAddStates,
    EduDeleteStates,
    EduEditStates,
    UserListStates,
    UserSearchStates,
)
from bot_app.config import get_settings
from bot_app.db.models import (
    EducationArticle,
    ManualLink,
    Panel,
    PaymentCard,
    PaymentRequest,
    Purchase,
    User,
)
from bot_app.providers.factory import get_provider_for_panel
from bot_app.security.crypto import encrypt_secret
from bot_app.services.app_settings import get_card_to_card_instruction, set_card_to_card_instruction
from bot_app.services.audit import audit_log
from bot_app.services.manual_links import bulk_import_links, deliver_one_link, parse_import_lines
from bot_app.services.traffic_sync import sync_batch
from bot_app.services.wallet import adjust_balance, try_approve_payment
from bot_app.utils.jalali_format import format_message, format_money

router = Router()
D = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
PAGE = 5


def _f(s: str) -> str:
    return format_message(s, include_footer=False)


def _d(s: str) -> str:
    return (s or "").strip().translate(D)


def _ssl_kb() -> ReplyKeyboardMarkup:
    b = ReplyKeyboardBuilder()
    b.row(T.BTN_SSL_ON, T.BTN_SSL_OFF)
    b.row(T.BTN_BACK_ADMIN)
    return b.as_markup(resize_keyboard=True)


def _is_priv(a) -> bool:
    return bool(a and is_owner_or_manager(a))


@router.message(F.text == T.BTN_BACK_ADMIN)
async def a_back(m: Message, session, st: FSMContext) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.clear()
    s = get_settings()
    await m.answer(
        _f(T.ADMIN_MENU_TITLE),
        reply_markup=admin_root_menu_kb(manual_enabled=s.manual_mode_enabled),
    )


@router.message(F.text == T.BTN_BACK_MAIN)
async def u_back(m: Message, session, st: FSMContext) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.clear()
    await m.answer(_f("منوی کاربری"), reply_markup=main_user_kb())


# ——— Add panel


@router.message(F.text.in_(T.BTN_ADD_PANEL_ALIASES))
async def ap_menu(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return await m.answer("فقط مالک/مدیر اصلی.")
    # Any previous FSM (shop/wallet/…) would block the type pick handler; clear so buttons always work
    await st.clear()
    await m.answer(
        _f("افزودن پنل — نوع؟"), reply_markup=admin_add_panel_kb()
    )


# Optional legacy labels (old Unicode arrow prefix) from cached keyboards
_OLDB_MZ = "⮕ مزربن (Marzban)"
_OLDB_3X = "⮕ 3x-ui / Sanaei"


@router.message(
    (F.text == T.BTN_TYPE_MARZBAN)
    | (F.text == T.BTN_TYPE_3XUI)
    | (F.text == _OLDB_MZ)
    | (F.text == _OLDB_3X)
)
async def ap_type(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    pty = "3xui" if m.text in (T.BTN_TYPE_3XUI, _OLDB_3X) else "marzban"
    await st.set_state(AddPanelStates.name)
    await st.update_data(pty=pty)
    lab = "3x-ui" if pty == "3xui" else "Marzban"
    await m.answer(
        _f(f"پنل {lab}\n\nنام داخلی (۲–۱۰۰ کاراکتر) را بفرستید:"),
        reply_markup=admin_cancel_row_kb(),
    )


@router.message(AddPanelStates, F.text == T.BTN_CANCEL)
async def ap_cx(m: Message, st: FSMContext) -> None:
    await st.clear()
    await m.answer("لغو.", reply_markup=admin_add_panel_kb())


@router.message(AddPanelStates.name)
async def ap_n(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    nm = (m.text or "").strip()
    if not 2 <= len(nm) <= 100:
        return await m.answer("طول بد.")
    await st.update_data(nm=nm)
    await st.set_state(AddPanelStates.base_url)
    await m.answer("https://... base_url؟")


@router.message(AddPanelStates.base_url)
async def ap_u(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    u0 = (m.text or "").strip()
    if not re.match(r"^https?://\S+$", u0):
        return await m.answer("URL بد.")
    u0 = u0.rstrip("/")
    d0 = await st.get_data()
    await st.update_data(purl=u0)
    if d0.get("pty") == "3xui":
        await st.set_state(AddPanelStates.web_path)
        return await m.answer("web path؟ (مثلا /xui یا -)")
    await st.set_state(AddPanelStates.username)
    await m.answer("username؟")


@router.message(AddPanelStates.web_path)
async def ap_wp(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    t0 = (m.text or "").strip()
    w = None
    if t0 and t0 not in ("-", ".", "—"):
        w = t0 if t0.startswith("/") else f"/{t0}"
    await st.update_data(pwb=w)
    await st.set_state(AddPanelStates.username)
    await m.answer("username؟")


@router.message(AddPanelStates.username)
async def ap_usr(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    u0 = (m.text or "").strip()
    if not u0:
        return
    await st.update_data(pu=u0)
    await st.set_state(AddPanelStates.password)
    await m.answer("password؟")


@router.message(AddPanelStates.password)
async def ap_pwd(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    pw0 = m.text or ""
    if len(pw0) < 2:
        return await m.answer("کوتاه.")
    cfg = get_settings()
    d0 = await st.get_data()
    enc = encrypt_secret(cfg.panel_credential_encryption_key, pw0)
    await st.update_data(pe=enc)
    if d0.get("pty") == "marzban":
        await st.set_state(AddPanelStates.marzban_token)
        return await m.answer("API token؟ (یا -)")
    await st.set_state(AddPanelStates.inbound)
    await m.answer("inbound (عدد) / 0")


@router.message(AddPanelStates.marzban_token)
async def ap_tok(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    t0 = (m.text or "").strip()
    cfg = get_settings()
    if t0 and t0 not in ("-", "—"):
        enc = encrypt_secret(
            cfg.panel_credential_encryption_key, t0
        )
        await st.update_data(mt=enc)
    else:
        await st.update_data(mt=None)
    await st.set_state(AddPanelStates.verify_ssl)
    await m.answer("SSL؟", reply_markup=_ssl_kb())


@router.message(AddPanelStates.inbound)
async def ap_in(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    v = _d(m.text or "0")
    n = int(v) if re.match(r"^-?\d+$", v) else 0
    await st.update_data(ibi=None if n == 0 else n)
    await st.set_state(AddPanelStates.verify_ssl)
    await m.answer("SSL؟", reply_markup=_ssl_kb())


@router.message(
    AddPanelStates.verify_ssl, F.text.in_((T.BTN_SSL_ON, T.BTN_SSL_OFF))
)
async def ap_sv(
    m: Message, session, st: FSMContext
) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    cfg = get_settings()
    d0 = await st.get_data()
    dbp = "sanaei_3xui" if d0.get("pty") == "3xui" else "marzban"
    row = Panel(
        name=(d0.get("nm") or "P")[:255],
        type=dbp,
        base_url=(d0.get("purl") or "")[:512],
        web_base_path=d0.get("pwb"),
        username=(d0.get("pu") or "")[:255],
        password_encrypted=d0.get("pe", ""),
        api_token_encrypted=d0.get("mt"),
        verify_ssl=(m.text == T.BTN_SSL_ON),
        timeout_seconds=30,
        inbound_id=d0.get("ibi"),
        is_active=True,
    )
    session.add(row)
    await session.flush()
    pr0 = (await session.execute(
        select(Panel).where(Panel.id == row.id)
    )).scalar_one()
    rid0 = str(uuid.uuid4())[:10]
    pv = get_provider_for_panel(
        pr0, request_id=rid0, encryption_key=cfg.panel_credential_encryption_key
    )
    t0 = await pv.test_connection()
    pr0.last_test_status = "ok" if t0.ok else "fail"
    pr0.last_test_error = (t0.error_message or "")[:2000] if not t0.ok else None
    pr0.last_test_at = datetime.now(timezone.utc)
    await session.commit()
    await st.clear()
    s0 = "موفق" if t0.ok else "ناموفق"
    msg0 = f"پنل id=<code>{row.id}</code>  تست: {s0}"
    if t0.error_message and not t0.ok:
        msg0 += "\n" + html.escape(t0.error_message[:500])
    await m.answer(
        _f(msg0), parse_mode="HTML",
        reply_markup=admin_root_menu_kb(
            manual_enabled=cfg.manual_mode_enabled
        ),
    )


# ——— Users


@router.message(F.text == T.BTN_USER_MGMT)
async def u_menu(m: Message, session) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await m.answer(_f("مدیریت کاربران"), reply_markup=admin_user_menu_kb())


@router.message(F.text == T.BTN_USER_SEARCH)
async def u_q0(m: Message, session, st: FSMContext) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.set_state(UserSearchStates.query)
    await m.answer("telegram id:", reply_markup=admin_cancel_row_kb())


@router.message(UserSearchStates.query, F.text)
async def u_q1(m: Message, session, st: FSMContext) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await u_menu(m, session)  # type: ignore[func-returns-value]
    tgid = int(_d(m.text or "0") or 0)
    u0 = (await session.execute(
        select(User).where(User.telegram_id == tgid)
    )).scalar_one_or_none()
    await st.clear()
    if not u0:
        return await m.answer("نیافت.", reply_markup=admin_user_menu_kb())
    await m.answer(
        _f(
            f"id {u0.id}  tg{u0.telegram_id} @{u0.username or '—'} \n"
            f"مانده: {format_money(int(u0.wallet_balance))} \n"
            f"یاد: {(u0.admin_note or '—')[:200]}\n"
            f"blok: {u0.is_blocked}"
        ),
        reply_markup=admin_user_menu_kb(),
    )


async def _u_page(
    m: Message, session, st: FSMContext, p: int
) -> None:
    tot0 = (await session.execute(
        select(func.count()).select_from(User)
    )).scalar_one() or 0
    pmax = max(0, (int(tot0) + PAGE - 1) // PAGE - 1)
    p1 = min(max(0, p), pmax)
    off0 = p1 * PAGE
    r0 = await session.execute(
        select(User).order_by(
            desc(User.id)
        ).offset(off0).limit(PAGE)
    )
    rows0: Sequence[User] = r0.scalars().all()
    if not rows0:
        await st.clear()
        return await m.answer("—", reply_markup=admin_user_menu_kb())
    o: List[str] = [f"صفحه {p1+1}/{pmax+1}  n={int(tot0)}"]
    for u0 in rows0:
        o.append(
            f"#{u0.id} tg{u0.telegram_id}  {format_money(int(u0.wallet_balance))}ت"
        )
    await st.set_state(UserListStates.page)
    await st.update_data(ulp=p1)
    await m.answer(_f("\n".join(o)), reply_markup=admin_user_list_nav_kb())


@router.message(F.text == T.BTN_USER_LIST)
async def u_l0(m: Message, session, st: FSMContext) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.set_state(UserListStates.page)
    await _u_page(m, session, st, 0)


@router.message(UserListStates.page, F.text == T.BTN_PAGE_PREV)
async def u_prev(m: Message, session, st: FSMContext) -> None:
    p0 = int((await st.get_data()).get("ulp", 0))
    await _u_page(m, session, st, p0 - 1)


@router.message(UserListStates.page, F.text == T.BTN_PAGE_NEXT)
async def u_next(m: Message, session, st: FSMContext) -> None:
    p0 = int((await st.get_data()).get("ulp", 0))
    await _u_page(m, session, st, p0 + 1)


@router.message(F.text == T.BTN_USER_BULK_CREDIT)
async def bc0(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return await m.answer("فقط مالک/مدیر.")
    await st.set_state(BulkCreditStates.user_ids)
    await m.answer("tg id ها (کاما/فاصله):", reply_markup=admin_cancel_row_kb())


@router.message(BulkCreditStates.user_ids, F.text)
async def bc1(m: Message, st: FSMContext) -> None:
    if m.text == T.BTN_CANCEL:
        await st.clear()
        s = get_settings()
        return await m.answer("—", reply_markup=admin_user_menu_kb())  # type: ignore
    tgs0: list[int] = []
    for w0 in re.split(r"[\s,،]+", _d(m.text or "")):
        w0 = w0.strip()
        if re.match(r"^-?\d+$", w0 or ""):
            tgs0.append(int(w0))
    tgs0 = list(dict.fromkeys(tgs0))
    if not tgs0:
        return await m.answer("هیچ.")
    await st.update_data(btg=tgs0)
    await st.set_state(BulkCreditStates.amount)
    await m.answer("مبلغ (تومان)؟")


@router.message(BulkCreditStates.amount, F.text)
async def bc2(m: Message, st: FSMContext) -> None:
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("—", reply_markup=admin_user_menu_kb())  # type: ignore
    a0 = int(_d(m.text or "0") or 0)
    if a0 <= 0 or a0 > 1_000_000_000:
        return await m.answer("مبلغ بد")
    d0 = await st.get_data()
    await st.update_data(bam=a0)
    await st.set_state(BulkCreditStates.confirm)
    tgs0: list = d0.get("btg", [])
    kb0 = ReplyKeyboardBuilder()
    kb0.button(T.BTN_CONFIRM)
    kb0.button(T.BTN_CANCEL)
    await m.answer(
        f"تایید: {len(tgs0)} × {format_money(a0)}؟", reply_markup=kb0.as_markup(
            resize_keyboard=True
        )
    )


@router.message(BulkCreditStates.confirm, F.text == T.BTN_CANCEL)
async def bc3x(m: Message, st: FSMContext) -> None:
    await st.clear()
    await m.answer("لغو", reply_markup=admin_user_menu_kb())


@router.message(BulkCreditStates.confirm, F.text == T.BTN_CONFIRM)
async def bc3o(
    m: Message, session, st: FSMContext
) -> None:
    ad0 = await get_admin(session, m.from_user.id)
    if not _is_priv(ad0):
        return
    d0 = await st.get_data()
    tgs0: list = d0.get("btg", [])
    am0 = int(d0.get("bam", 0))
    rid0 = str(uuid.uuid4())[:12]
    n0 = 0
    for tg0 in tgs0:
        u0 = (await session.execute(
            select(User).where(User.telegram_id == tg0)
        )).scalar_one_or_none()
        if not u0:
            continue
        ok0, _, _ = await adjust_balance(
            session,
            user_id=u0.id, delta=am0, tx_type="manual_adjustment",
            reference="bulk", request_id=rid0
        )
        n0 += int(bool(ok0))
    await session.commit()
    await st.clear()
    await audit_log(
        session, action="admin_bulk", admin_telegram_id=m.from_user.id, details=rid0
    )
    await session.commit()
    await m.answer(
        f"انجام: {n0}/{len(tgs0)}", reply_markup=admin_user_menu_kb()
    )


@router.message(F.text == T.BTN_USER_BROADCAST)
async def br0(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    await st.set_state(BroadcastStates.text)
    await m.answer(
        "سطر1: `همه` یا tg id ها. سطر2+ متن. {name} {brand}\n(بدون تگ اگر مفسل)",
        reply_markup=admin_cancel_row_kb(),
    )


@router.message(BroadcastStates.text, F.text)
async def br1(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("—", reply_markup=admin_user_menu_kb())  # type: ignore
    li0 = (m.text or "").splitlines()
    if not li0:
        return
    h0, *rest0 = li0
    bdy0 = "\n".join(rest0).strip()
    brand0 = get_settings().brand_name
    if h0.strip().lower() in ("all", "همه"):
        ids0 = [int(t) for t in (await session.execute(
            select(User.telegram_id)
        )).scalars().all() if t is not None]
        txt0 = bdy0
    else:
        ids0 = []
        for w0 in re.split(r"[\s,،]+", _d(h0)):
            w0 = w0.strip()
            if w0 and re.match(r"^-?\d+$", w0):
                ids0.append(int(w0))
        txt0 = bdy0 or h0
    if not ids0:
        return await m.answer("هدف بد", reply_markup=admin_user_menu_kb())
    nok = 0
    for tid0 in ids0:
        tmsg = (txt0 or " ").replace("{name}", "کاربر").replace("{brand}", brand0)
        try:
            await m.bot.send_message(int(tid0), tmsg)
            nok += 1
        except Exception:  # noqa: BLE001
            pass
    await st.clear()
    await m.answer(
        f"نتیجه: {nok}/{len(ids0)} (در صورت بلاک، خطا رد می‌شود)",
        reply_markup=admin_user_menu_kb(),
    )


# ——— Finance


@router.message(F.text == T.BTN_FINANCE)
async def fn_menu(m: Message, session) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    snip = (await get_card_to_card_instruction(session))[:200]
    await m.answer(
        _f("مالی\n" + f"نمونه راهنما:\n{html.escape(snip)}..."),
        parse_mode="HTML",
        reply_markup=admin_finance_menu_kb(),
    )


@router.message(F.text == T.BTN_C2C_TEXT)
async def c2c0(m: Message, session, st: FSMContext) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    cur = await get_card_to_card_instruction(session)
    await st.set_state(C2CTextStates.text)
    await m.answer(
        f"متن فعلی:\n{html.escape(cur[:2000])}\n\n— متن جدید را بفرستید (یا انصراف):",
        parse_mode="HTML", reply_markup=admin_cancel_row_kb()
    )


@router.message(C2CTextStates.text, F.text)
async def c2c1(
    m: Message, session, st: FSMContext
) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("—", reply_markup=admin_finance_menu_kb())  # type: ignore
    ntxt = m.text or ""
    if not ntxt.strip():
        return
    await set_card_to_card_instruction(session, ntxt.strip())
    await st.clear()
    await session.commit()
    await m.answer(
        "ذخیره شد.", reply_markup=admin_finance_menu_kb()
    )


@router.message(F.text == T.BTN_CARDS)
async def cards_list(
    m: Message, session
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    r0 = (await session.execute(
        select(PaymentCard).order_by(PaymentCard.id.desc()).limit(15)
    )).scalars().all()
    if not r0:
        return await m.answer(
            "هیچ کارتی.", reply_markup=admin_finance_menu_kb()
        )
    out: List[str] = ["کارت‌های ثبت‌شده (۱۵)"]
    for c0 in r0:
        s0 = "✓" if c0.is_active else "✕"
        out.append(
            f"#{c0.id} {s0} {c0.card_number} — {c0.bank_name} ({c0.card_holder_name})"
        )
    await m.answer(
        _f("\n".join(out))[:4000], reply_markup=admin_finance_menu_kb()
    )


@router.message(F.text == T.BTN_ADD_CARD)
async def addc0(
    m: Message, session, st: FSMContext
) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    await st.set_state(AddCardStates.number)
    await m.answer(
        "شماره کارت (۱۶ رقم) سپس در گام‌های بعد: نام، بانک، تایید",
        reply_markup=admin_cancel_row_kb(),
    )


@router.message(AddCardStates, F.text == T.BTN_CANCEL)
async def addc_x(
    m: Message, st: FSMContext
) -> None:
    await st.clear()
    await m.answer("لغو.", reply_markup=admin_finance_menu_kb())


@router.message(AddCardStates.number, F.text)
async def addc_n(
    m: Message, st: FSMContext
) -> None:
    d0 = re.sub(r"\D", "", _d(m.text or ""))
    if len(d0) < 10:
        return await m.answer("شماره ناقص.")
    await st.update_data(cn=d0)
    await st.set_state(AddCardStates.holder)
    await m.answer("نام صاحب کارت؟")


@router.message(AddCardStates.holder, F.text)
async def addc_h(
    m: Message, st: FSMContext
) -> None:
    h0 = (m.text or "").strip()
    if len(h0) < 2:
        return
    await st.update_data(ch=h0)
    await st.set_state(AddCardStates.bank)
    await m.answer("نام بانک؟")


@router.message(AddCardStates.bank, F.text)
async def addc_b(
    m: Message, st: FSMContext
) -> None:
    b0 = (m.text or "").strip()
    if len(b0) < 2:
        return
    await st.update_data(cb=b0)
    await st.set_state(AddCardStates.confirm)
    d0 = await st.get_data()
    kb0 = ReplyKeyboardBuilder()
    kb0.button(T.BTN_CONFIRM)
    await m.answer(
        f"ثبت؟\n{d0.get('cn')} — {d0.get('ch')} / {d0.get('cb')}",
        reply_markup=kb0.as_markup(resize_keyboard=True),
    )


@router.message(AddCardStates.confirm, F.text == T.BTN_CONFIRM)
async def addc_o(
    m: Message, session, st: FSMContext
) -> None:
    if not _is_priv(await get_admin(session, m.from_user.id)):
        return
    d0 = await st.get_data()
    session.add(
        PaymentCard(
            card_number=(d0.get("cn") or "")[:32],
            card_holder_name=(d0.get("ch") or "")[:255],
            bank_name=(d0.get("cb") or "")[:255],
            is_active=True,
        )
    )
    await st.clear()
    await session.commit()
    await m.answer("کارت اضافه شد.", reply_markup=admin_finance_menu_kb())


@router.message(F.text == T.BTN_PENDING_PAY)
async def pend0(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0:
        return
    r0 = await session.execute(
        select(PaymentRequest, User, PaymentCard)
        .join(User, User.id == PaymentRequest.user_id)
        .join(PaymentCard, PaymentCard.id == PaymentRequest.card_id)
        .where(PaymentRequest.status == "pending")
        .order_by(PaymentRequest.id.asc())
        .limit(10)
    )
    rows0 = r0.all()
    if not rows0:
        return await m.answer(
            "هیچ درخواست معلقی.", reply_markup=admin_finance_menu_kb()
        )
    lines0: List[str] = ["تایید با فرمت: تایید پرداخت id=۱۲۳ (یا `رد` id)"]
    for pr0, u0, c0 in rows0:
        lines0.append(
            f"#{pr0.id}  user {u0.telegram_id}  {format_money(int(pr0.amount))}  کارت …{c0.card_number[-4:]}"
        )
    await m.answer(
        _f("\n".join(lines0))[:4000], reply_markup=admin_finance_menu_kb()
    )


@router.message(F.text.regexp(r"^تایید پرداخت id=\d+$"))
async def pay_yes(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0:
        return
    mm = re.search(
        r"id=(\d+)", m.text or "", re.I
    )
    if not mm:
        return
    pid0 = int(mm.group(1))
    rid0 = str(uuid.uuid4())[:12]
    st0, udb = await try_approve_payment(
        session, payment_request_id=pid0, admin_db_id=a0.id, request_id=rid0
    )
    if st0 not in ("approved",):
        await session.rollback()
        return await m.answer(f"نشد: {st0}", reply_markup=admin_finance_menu_kb())
    await session.commit()
    if udb is not None:
        ur = (await session.execute(select(User).where(User.id == udb))).scalar_one_or_none()
        if ur:
            try:
                await m.bot.send_message(
                    int(ur.telegram_id),
                    _f(f"پرداخت شما تایید و به کیف پول اضافه شد. شناسه: {pid0}"),
                )
            except Exception:  # noqa: BLE001
                pass
    await m.answer("تایید شد.", reply_markup=admin_finance_menu_kb())


@router.message(F.text.regexp(r"^رد id=\d+$"))
async def pay_no(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0:
        return
    mm = re.search(
        r"id\s*=\s*(\d+)", m.text or "", re.I
    )
    if not mm:
        return
    pid0 = int(mm.group(1))
    pr0 = (await session.execute(
        select(PaymentRequest).where(
            and_(PaymentRequest.id == pid0, PaymentRequest.status == "pending")
        )
    )).scalar_one_or_none()
    if not pr0:
        return await m.answer("نیافت/قبلاً")
    pr0.status = "rejected"
    pr0.approved_by_admin_id = a0.id
    pr0.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await m.answer("رد شد.", reply_markup=admin_finance_menu_kb())


# ——— Sales + stock (manual) ———


@router.message(F.text == T.BTN_SALES)
async def s_menu(
    m: Message, session
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    s0 = get_settings()
    await m.answer(
        _f("فروش و موجودی: شمارش لینک + ایمپورت/تحویل (دستی) اگر فعال باشد."),
        reply_markup=admin_sales_menu_kb(
            manual_enabled=s0.manual_mode_enabled
        ),
    )


@router.message(F.text == T.BTN_LIST_PANELS)
async def list_p(
    m: Message, session
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    r0 = (await session.execute(
        select(Panel).order_by(
            desc(Panel.id)
        ).limit(20)
    )).scalars().all()
    if not r0:
        return await m.answer(
            "هیچ پنلی در دیتابیس ثبت نیست. از ➕ افزودن پنل اضافه کنید."
        )
    s0: List[str] = ["فهرست پنل (۲۰ ردیف)"]
    for p0 in r0:
        stt = p0.last_test_status or "—"
        s0.append(
            f"#{p0.id}  {p0.name}  ({p0.type})  t={stt}"
        )
    await m.answer(
        _f("\n".join(s0))[:4000], reply_markup=admin_root_menu_kb(
            manual_enabled=get_settings().manual_mode_enabled
        ),
    )


@router.message(F.text == T.BTN_IMPORT, StateFilter(None))
async def imp_hint(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    s0 = get_settings()
    if not a0 or not s0.manual_mode_enabled:
        return await m.answer("حالت دستی غیرفعال است.")
    await m.answer(
        "خط۱: <code>server_id,plan_id</code> سپس هر خط یک لینک.\nمثال:\n3,5\nhttps://...",
        parse_mode="HTML",
        reply_markup=admin_sales_menu_kb(manual_enabled=True),
    )


@router.message(F.text.regexp(r"^\d+,\d+\s*\n"), StateFilter(None))
async def imp_run(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    s0 = get_settings()
    if not a0 or not s0.manual_mode_enabled:
        return
    li0 = (m.text or "").splitlines()
    h0 = li0[0].replace(" ", "")
    a_s, p_s = h0.split(",")
    sid0, plan0 = int(a_s), int(p_s)
    body0 = "\n".join(li0[1:])
    parsed0 = parse_import_lines(body0, s0.max_import_links)
    rid0 = str(uuid.uuid4())[:10]
    stats0 = await bulk_import_links(
        session,
        lines=parsed0,
        manual_server_id=sid0,
        manual_plan_id=plan0,
        admin_db_id=a0.id,
        max_links=s0.max_import_links,
        max_link_length=4096,
        request_id=rid0,
    )
    await session.commit()
    await m.answer(
        _f(
            f"ایمپورت: دریافت {stats0['total']}  اضافه {stats0['added']}  "
            f"تکرار-فایل {stats0['duplicate_in_file']}  تکرار-دیتابیس {stats0['duplicate_in_db']}"
        ),
        reply_markup=admin_sales_menu_kb(manual_enabled=True),
    )


@router.message(F.text == T.BTN_DELIVER, StateFilter(None))
async def deliv_hint(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    s0 = get_settings()
    if not a0 or not s0.manual_mode_enabled:
        return
    await m.answer(
        "فرمت:\n"
        "<code>تحویل server_id=3 plan_id=5 tg=123456789</code>\n"
        "یا tg=0 فقط رکورد ادمین.",
        parse_mode="HTML",
        reply_markup=admin_sales_menu_kb(manual_enabled=True),
    )


@router.message(F.text.startswith("تحویل "), StateFilter(None))
async def deliv_run(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    s0 = get_settings()
    if not a0 or not s0.manual_mode_enabled:
        return
    try:
        parts = dict(p.split("=") for p in (m.text or "").split()[1:])
        sid0 = int(parts.get("server_id", 0))
        plan0 = int(parts.get("plan_id", 0))
        tg0 = int(parts.get("tg", 0))
    except Exception:  # noqa: BLE001
        return await m.answer("فرمت بد")
    rid0 = str(uuid.uuid4())[:10]
    ok0, _, d0 = await deliver_one_link(
        session,
        manual_server_id=sid0,
        manual_plan_id=plan0,
        admin_db_id=a0.id,
        user_telegram_id=tg0 or None,
        customer_info=None,
        request_id=rid0,
    )
    if not ok0:
        await session.rollback()
        return await m.answer("تحویل انجام نشد.", reply_markup=admin_sales_menu_kb(manual_enabled=True))
    await session.commit()
    await m.answer(
        _f(
            f"تحویل #{d0['delivery_id']}\n"
            f"<code>{html.escape(d0['link'][:2000])}</code>"
        ),
        parse_mode="HTML",
        reply_markup=admin_sales_menu_kb(manual_enabled=True),
    )


@router.message(F.text == T.BTN_LINK_STOCK, StateFilter(None))
async def stock0(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    s0 = get_settings()
    if not a0 or not s0.manual_mode_enabled:
        return
    u0 = (
        await session.execute(
            select(func.count())
            .select_from(ManualLink)
            .where(ManualLink.status == "unused", ManualLink.is_active.is_(True))
        )
    ).scalar_one() or 0
    await m.answer(
        _f(f"لینک‌های آماده (unused): {int(u0)}"),
        reply_markup=admin_sales_menu_kb(manual_enabled=True),
    )


# ——— Reports ———


@router.message(F.text == T.BTN_REPORTS)
async def rep0(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0 or not _is_priv(a0):
        return
    s0 = get_settings()
    uu = (await session.execute(select(func.count()).select_from(User))).scalar_one() or 0
    if s0.api_products_enabled:
        q0 = (await session.execute(
            select(func.coalesce(func.sum(Purchase.price), 0))
            .select_from(Purchase)
            .where(
                Purchase.status == "completed",
                Purchase.purchase_type == "api",
            )
        )).scalar()
        tot = int(q0 or 0)
    else:
        tot = 0
    await m.answer(
        _f(
            f"گزارش سریع: کاربران: {int(uu)}\n"
            f"جمع فروش API (تکمیل‌شده): {format_money(tot)} تومان"
        ),
        reply_markup=admin_root_menu_kb(manual_enabled=s0.manual_mode_enabled),
    )


# ——— System ———


def _session_maker():
    s0 = get_settings()
    from bot_app.db.session import async_session_factory

    return async_session_factory(s0.database_url)


@router.message(F.text == T.BTN_SYSTEM)
async def sys_menu(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0 or not _is_priv(a0):
        return
    s0 = get_settings()
    await m.answer(
        _f("سینک / سلامت دیتابیس"),
        reply_markup=admin_system_menu_kb(),
    )


@router.message(F.text == T.BTN_SYNC, StateFilter(None))
async def run_sync(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0 or not _is_priv(a0):
        return
    fac = _session_maker()
    n0 = await sync_batch(fac, settings=get_settings())
    await m.answer(
        f"سینک اجرا شد. تعداد: {n0} (بچ طبق تنظیمات)",
        reply_markup=admin_system_menu_kb(),
    )


@router.message(F.text == T.BTN_DB_HEALTH, StateFilter(None))
async def db_h(
    m: Message, session
) -> None:
    a0 = await get_admin(session, m.from_user.id)
    if not a0 or not _is_priv(a0):
        return
    try:
        await session.execute(text("SELECT 1"))
        await m.answer("دیتابیس: OK", reply_markup=admin_system_menu_kb())
    except Exception as e:  # noqa: BLE001
        await m.answer(
            f"خطا: {e}", reply_markup=admin_system_menu_kb()
        )


# ——— Education ———


@router.message(F.text == T.BTN_EDUCATION)
async def edu_menu(
    m: Message, session
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await m.answer(
        _f("بخش آموزش: افزودن/ویرایش/حذف/فهرست"),
        reply_markup=admin_education_menu_kb(),
    )


@router.message(F.text == T.BTN_EDU_LIST, StateFilter(None))
async def edu_list(
    m: Message, session
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    r0 = (await session.execute(
        select(EducationArticle).order_by(
            EducationArticle.sort_order.asc(), EducationArticle.id.desc()
        ).limit(20)
    )).scalars().all()
    if not r0:
        return await m.answer("هنوز رکوردی نیست.", reply_markup=admin_education_menu_kb())
    o0: List[str] = ["فهرست (۲۰) — ویرایش/حذف با id"]
    for a0 in r0:
        o0.append(
            f"#{a0.id}  {'on' if a0.is_active else 'off'}  {a0.title[:40]}"
        )
    await m.answer(
        _f("\n".join(o0))[:4000], reply_markup=admin_education_menu_kb()
    )


@router.message(F.text == T.BTN_EDU_ADD, StateFilter(None))
async def edu_add0(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.set_state(EduAddStates.title)
    await m.answer("عنوان؟", reply_markup=admin_cancel_row_kb())


@router.message(EduAddStates.title, F.text)
async def edu_add1(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("لغو.", reply_markup=admin_education_menu_kb())  # type: ignore
    t0 = (m.text or "").strip()
    if len(t0) < 2:
        return
    await st.update_data(et=t0)
    await st.set_state(EduAddStates.body)
    await m.answer("متن؟ (چند خط)")


@router.message(EduAddStates.body, F.text)
async def edu_add2(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("لغو.", reply_markup=admin_education_menu_kb())  # type: ignore
    b0 = (m.text or "").strip()
    d0 = await st.get_data()
    session.add(
        EducationArticle(
            title=(d0.get("et") or "—")[:255],
            body_text=b0,
            is_active=True,
            sort_order=0,
        )
    )
    await st.clear()
    await session.commit()
    await m.answer("ثبت شد.", reply_markup=admin_education_menu_kb())


@router.message(F.text == T.BTN_EDU_EDIT, StateFilter(None))
async def edu_e0(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.set_state(EduEditStates.id)
    await m.answer("شناسه رکورد (عدد)؟", reply_markup=admin_cancel_row_kb())


@router.message(EduEditStates.id, F.text)
async def edu_e1(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("لغو.", reply_markup=admin_education_menu_kb())  # type: ignore
    try:
        eid = int(_d(m.text or "0"))
    except ValueError:
        return await m.answer("عدد نیست.")
    row = (await session.execute(
        select(EducationArticle).where(EducationArticle.id == eid)
    )).scalar_one_or_none()
    if not row:
        return await m.answer("نیافت.")
    await st.update_data(eid=eid)
    await st.set_state(EduEditStates.title)
    await m.answer(
        f"عنوان فعلی: {row.title}\nعنوان جدید؟ (یا - برای عدم تغییر)"
    )


@router.message(EduEditStates.title, F.text)
async def edu_e2(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    t0 = (m.text or "").strip()
    d0 = await st.get_data()
    eid = int(d0.get("eid", 0))
    row = (await session.execute(
        select(EducationArticle).where(EducationArticle.id == eid)
    )).scalar_one_or_none()
    if not row:
        await st.clear()
        return
    if t0 and t0 != "-":
        row.title = t0[:255]
    await st.set_state(EduEditStates.body)
    await m.answer("متن جدید؟ (یا - برای عدم تغییر)")


@router.message(EduEditStates.body, F.text)
async def edu_e3(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    b0 = (m.text or "").strip()
    d0 = await st.get_data()
    eid = int(d0.get("eid", 0))
    row = (await session.execute(
        select(EducationArticle).where(EducationArticle.id == eid)
    )).scalar_one_or_none()
    if not row:
        await st.clear()
        return
    if b0 and b0 != "-":
        row.body_text = b0
    await st.clear()
    await session.commit()
    await m.answer("ذخیره شد.", reply_markup=admin_education_menu_kb())


@router.message(F.text == T.BTN_EDU_DEL, StateFilter(None))
async def edu_d0(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    await st.set_state(EduDeleteStates.id)
    await m.answer("حذف: شناسه؟", reply_markup=admin_cancel_row_kb())


@router.message(EduDeleteStates.id, F.text)
async def edu_d1(
    m: Message, session, st: FSMContext
) -> None:
    if not await get_admin(session, m.from_user.id):
        return
    if m.text == T.BTN_CANCEL:
        await st.clear()
        return await m.answer("لغو.", reply_markup=admin_education_menu_kb())  # type: ignore
    try:
        eid = int(_d(m.text or "0"))
    except ValueError:
        return await m.answer("عدد نیست.")
    row = (await session.execute(
        select(EducationArticle).where(EducationArticle.id == eid)
    )).scalar_one_or_none()
    if not row:
        await st.clear()
        return await m.answer("نیافت.", reply_markup=admin_education_menu_kb())  # type: ignore
    await session.delete(row)
    await st.clear()
    await session.commit()
    await m.answer("حذف شد.", reply_markup=admin_education_menu_kb())