"""Хендлеры синхронизации с облачными дисками."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.handlers_core import _ensure_user
from app.bot.keyboards import back_to_menu, sync_kb
from app.core import user_settings as us
from app.core.i18n import tr
from app.core.users import get_accounts
from app.db.session import session_scope
from app.sync.engine import last_logs, sync_user

log = logging.getLogger("trambot.bot")
router = Router(name="sync")

TITLES = {"google": "Google Drive", "yandex": "Яндекс Диск"}


async def _linked(s: AsyncSession, uid: str) -> dict:
    accs = await get_accounts(s, uid)
    return {a.provider: a for a in accs}


@router.message(Command("sync"))
async def cmd_sync(message: Message, command: CommandObject):
    uid, locale = await _ensure_user(message)
    provider = (command.args or "").strip().lower()
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
        accounts = await _linked(s, uid)
        if provider in TITLES:
            await _run(s, message, uid, provider, locale, prefs)
            return
        lines = ["\U0001f504 <b>Синхронизация</b>\n\n"]
        for p, title in TITLES.items():
            if accounts.get(p):
                lines.append(f"✅ {title} — папка <code>{prefs['sync_folder']}</code>")
            else:
                lines.append(f"➖ {title} — не привязан")
        lines.append(
            f"\nНаправление: {'двусторонняя' if prefs['sync_both_ways'] else 'только в облако'}\n"
            f"Автосинхронизация: {'вкл, раз в ' + str(prefs['auto_sync_interval_min']) + ' мин' if prefs['auto_sync'] else 'выкл'}"
        )
        logs = await last_logs(s, uid, 5)
        if logs:
            lines.append("\n<b>История:</b>")
            for lg in logs:
                mark = "✅" if lg.status == "ok" else "❌"
                lines.append(
                    f"{mark} {lg.provider} ↑{lg.uploaded} ↓{lg.downloaded} "
                    f"{lg.created_at.strftime('%d.%m %H:%M')}"
                )
    await message.answer("\n".join(lines), reply_markup=sync_kb(accounts, prefs, locale), parse_mode="HTML")


@router.callback_query(F.data == "menu:sync")
async def cb_sync(call: CallbackQuery):
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
        accounts = await _linked(s, uid)
    lines = ["\U0001f504 <b>Синхронизация</b>\n\n"]
    for p, title in TITLES.items():
        mark = "✅ привязан" if accounts.get(p) else "➖ не привязан"
        lines.append(f"{title} — {mark}")
    lines.append(f"\nПапка: <code>{prefs['sync_folder']}</code>")
    lines.append(f"Направление: {'двусторонняя' if prefs['sync_both_ways'] else 'только в облако'}")
    await call.message.edit_text("\n".join(lines), reply_markup=sync_kb(accounts, prefs, locale), parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data.startswith("sync:go:"))
async def cb_sync_go(call: CallbackQuery):
    provider = call.data.split(":")[-1]
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
        accounts = await _linked(s, uid)
        if not accounts.get(provider):
            await call.answer(f"{TITLES.get(provider)} не привязан", show_alert=True)
            return
        await call.message.edit_text("⏳ Синхронизация началась…")
        await _run(s, call.message, uid, provider, locale, prefs, edit=False)
    await call.answer()


async def _run(
    session: AsyncSession,
    message: Message,
    uid: str,
    provider: str,
    locale: str,
    prefs: dict,
    edit: bool = True,
) -> None:
    notify = prefs.get("notify_sync_done", True)
    status = await message.answer(f"⏳ Синхронизирую с {TITLES.get(provider, provider)}…")
    result = await sync_user(session, uid, provider)
    text = result.summary(locale)
    if result.uploaded or result.downloaded or result.deleted:
        details = []
        if result.downloaded:
            details.append("⬇️ " + ", ".join(result.downloaded[:5]))
        if result.uploaded:
            details.append("⬆️ " + ", ".join(result.uploaded[:5]))
        if result.deleted:
            details.append("\U0001f5d1 " + ", ".join(result.deleted[:5]))
        text += "\n\n" + "\n".join(details)
    if notify or result.status == "error":
        if edit:
            try:
                await status.edit_text(text, parse_mode="HTML")
                return
            except Exception:  # noqa: BLE001
                pass
        await message.answer(text, parse_mode="HTML", reply_markup=back_to_menu(locale))
    else:
        await status.delete()


@router.message(Command("syncstatus"))
async def cmd_syncstatus(message: Message):
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        logs = await last_logs(s, uid, 15)
    if not logs:
        await message.answer("Синхронизации ещё не было")
        return
    lines = ["\U0001f4cb <b>История синхронизации</b>\n"]
    for lg in logs:
        mark = "✅" if lg.status == "ok" else "❌"
        lines.append(
            f"{mark} {lg.created_at.strftime('%d.%m %H:%M')} · {lg.provider} · "
            f"↑{lg.uploaded} ↓{lg.downloaded} \U0001f5d1{lg.deleted}"
        )
        if lg.message:
            lines.append(f"    <code>{lg.message[:80]}</code>")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu(locale))
