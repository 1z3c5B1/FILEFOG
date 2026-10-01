"""Хендлеры: старт, меню, аккаунты, вход по OAuth."""
from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.helpers import usage_line
from app.bot.keyboards import accounts_kb, back_to_menu, main_menu
from app.config import settings
from app.core import user_settings as us
from app.core.i18n import tr
from app.core.users import get_accounts, get_by_tg, unlink_account, upsert_tg_user
from app.db.session import session_scope
from app.storage.factory import local_storage

log = logging.getLogger("trambot.bot")
router = Router(name="core")

HELP_RU = (
    "<b>Команды:</b>\n"
    "/menu — главное меню\n"
    "/files — список файлов\n"
    "/upload — загрузить файл\n"
    "/get &lt;путь&gt; — скачать файл\n"
    "/all — скачать всё архивом\n"
    "/mkdir &lt;имя&gt; — создать папку\n"
    "/del &lt;путь&gt; — удалить\n"
    "/accounts — привязанные аккаунты\n"
    "/link &lt;google|github|discord|yandex&gt; — войти\n"
    "/unlink &lt;провайдер&gt; — отвязать\n"
    "/sync — синхронизация с облаком\n"
    "/settings — все настройки\n"
    "/stats — статистика\n"
    "/help — эта справка"
)


async def _ensure_user(message: Message):
    tg_user = message.from_user
    async with session_scope() as s:
        user = await upsert_tg_user(
            s,
            tg_id=tg_user.id,
            username=tg_user.username or "",
            full_name=tg_user.first_name or "",
            is_admin=tg_user.id in settings.admin_ids,
        )
        return user.id, user.locale


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    await message.answer(
        f"{tr('welcome', locale, name=message.from_user.first_name or 'друг')}\n\n{tr('menu_text', locale)}\n\n"
        f"{await usage_line(uid, prefs)}",
        reply_markup=main_menu(locale),
        parse_mode="HTML",
    )
    if prefs.get("sync_on_start") and prefs.get("default_provider"):
        await _silent_sync(uid, prefs["default_provider"])


@router.message(Command("help", "h"))
async def cmd_help(message: Message):
    await message.answer(HELP_RU, parse_mode="HTML", reply_markup=back_to_menu())


@router.message(Command("menu", "start"))
async def cmd_menu(message: Message):
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    await message.answer(
        tr("menu_title", locale) + f"\n\n{await usage_line(uid, prefs)}",
        reply_markup=main_menu(locale),
        parse_mode="HTML",
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    uid, locale = await _ensure_user(message)
    local = local_storage(uid)
    files = local.walk()
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    size = local.total_size()
    text = (
        "\U0001f4ca <b>Статистика</b>\n\n"
        f"Файлов: {len(files)}\n"
        f"Занято: {await usage_line(uid, prefs)}\n"
        f"Облако: {prefs.get('default_provider') or '—'}\n"
        f"Папка синхронизации: {prefs.get('sync_folder')}\n"
        f"Автосинхронизация: {'вкл' if prefs.get('auto_sync') else 'выкл'}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=back_to_menu(locale))


# ------------------------------------------------------------------ меню

@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    uid, locale = await _ensure_user(call.message)
    await call.message.edit_text(
        tr("menu_text", locale), reply_markup=main_menu(locale), parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data == "menu:accounts")
async def cb_accounts(call: CallbackQuery):
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        accs = await get_accounts(s, uid)
    lines = ["\U0001f464 <b>Аккаунты</b>\n"]
    from app.core.oauth import ICONS, TITLES

    for p, title in TITLES.items():
        found = next((a for a in accs if a.provider == p), None)
        if found:
            lines.append(f"{ICONS[p]} <b>{title}</b> ✅ {found.login or found.email or ''}")
        else:
            lines.append(f"{ICONS[p]} {title} — не привязан")
    lines.append(
        f"\n\U0001f310 Панель: {settings.public_base_url.rstrip('/')}"
    )
    await call.message.edit_text(
        "\n".join(lines), reply_markup=accounts_kb({a.provider for a in accs}, locale), parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("acc:info:"))
async def cb_acc_info(call: CallbackQuery):
    provider = call.data.rsplit(":", 1)[-1]
    uid, locale = await _ensure_user(call.message)
    from app.core.oauth import ICONS, TITLES

    async with session_scope() as s:
        acc = next(
            (a for a in await get_accounts(s, uid) if a.provider == provider), None
        )
    if not acc:
        await call.answer(tr("not_linked", locale), show_alert=True)
        return
    tok = acc.token
    when = (
        time.strftime("%d.%m.%Y %H:%M", time.localtime(tok.expires_at))
        if tok and tok.expires_at
        else "—"
    )
    text = (
        f"{ICONS[provider]} <b>{TITLES[provider]}</b>\n\n"
        f"Профиль: {acc.login or acc.email or '—'}\n"
        f"Токен до: {when}\n"
        f"Refresh: {'есть' if tok and tok.refresh_token else 'нет'}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔓 Отвязать", callback_data=f"acc:unlink:{provider}"),
                InlineKeyboardButton(text=tr("back", locale), callback_data="menu:accounts"),
            ]
        ]
    )
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("acc:unlink:"))
async def cb_unlink(call: CallbackQuery):
    provider = call.data.rsplit(":", 1)[-1]
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        ok = await unlink_account(s, uid, provider)
    if ok:
        await call.answer(f"{provider} отвязан ✅", show_alert=True)
        await cb_accounts(call)
    else:
        await call.answer("Такой аккаунт не найден", show_alert=True)


@router.callback_query(F.data.startswith("acc:login:"))
async def cb_login(call: CallbackQuery):
    provider = call.data.split(":")[-1]
    uid, locale = await _ensure_user(call.message)
    if not settings.is_provider_configured(provider):
        await call.answer(f"{provider} не настроен в .env", show_alert=True)
        return
    from app.core.oauth import TITLES

    url = f"{settings.public_base_url.rstrip('/')}/auth/{provider}/login?tg_id={call.from_user.id}"
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"\U0001f517 Войти через {TITLES[provider]}", url=url)],
            [InlineKeyboardButton(text=tr("back", locale), callback_data="menu:accounts")],
        ]
    )
    await call.message.answer(
        f"Нажми кнопку и войди через <b>{TITLES[provider]}</b>.\n"
        "После входа бот пришлёт подтверждение.",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await call.answer()


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject):
    provider = (command.args or "").strip().lower()
    if provider not in ("google", "github", "discord", "yandex"):
        await message.answer(
            "Укажи провайдера: /link google | github | discord | yandex",
            reply_markup=back_to_menu(),
        )
        return
    uid, locale = await _ensure_user(message)
    if not settings.is_provider_configured(provider):
        await message.answer(f"{provider} не настроен в .env")
        return
    from app.core.oauth import TITLES
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    url = f"{settings.public_base_url.rstrip('/')}/auth/{provider}/login?tg_id={message.from_user.id}"
    await message.answer(
        f"Войди через <b>{TITLES[provider]}</b>:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="\U0001f517 Авторизовать", url=url)]]
        ),
        parse_mode="HTML",
    )


@router.message(Command("unlink", "logout"))
async def cmd_unlink(message: Message, command: CommandObject):
    provider = (command.args or "").strip().lower()
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        if not provider:
            accs = await get_accounts(s, uid)
            if not accs:
                await message.answer("Нет привязанных аккаунтов.")
                return
            lines = ["Отвязать какой аккаунт?\n"]
            for a in accs:
                lines.append(f"/unlink {a.provider}")
            await message.answer("\n".join(lines))
            return
        ok = await unlink_account(s, uid, provider)
    await message.answer("Отвязано ✅" if ok else "Такой аккаунт не найден")


@router.message(Command("accounts"))
async def cmd_accounts(message: Message):
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        accs = await get_accounts(s, uid)
    from app.core.oauth import ICONS, TITLES

    lines = ["\U0001f464 <b>Аккаунты</b>\n"]
    for p, title in TITLES.items():
        found = next((a for a in accs if a.provider == p), None)
        lines.append(
            f"{ICONS[p]} <b>{title}</b> ✅ {found.login or ''}" if found else f"{ICONS[p]} {title} — нет"
        )
    lines.append(f"\n\U0001f310 Панель: {settings.public_base_url.rstrip('/')}")
    await message.answer("\n".join(lines), reply_markup=accounts_kb({a.provider for a in accs}, locale), parse_mode="HTML")


async def _silent_sync(uid: str, provider: str) -> None:
    from app.sync.engine import sync_user

    async with session_scope() as s:
        try:
            await sync_user(s, uid, provider)
        except Exception:  # noqa: BLE001
            log.exception("sync on start failed")
