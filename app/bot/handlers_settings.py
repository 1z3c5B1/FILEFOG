"""Хендлеры настроек (/settings) с кнопками и вводом значений."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.handlers_core import _ensure_user
from app.bot.keyboards import back_to_menu, settings_kb
from app.config import settings as app_settings
from app.core import user_settings as us
from app.core.i18n import tr
from app.db.session import session_scope

log = logging.getLogger("trambot.bot")
router = Router(name="settings")


class WaitingValue(StatesGroup):
    value = State()


SECTION_TITLES = {
    "general": "\U0001f6e0 Общие",
    "files": "\U0001f5c2 Файлы",
    "sync": "\U0001f504 Синхронизация",
    "notify": "\U0001f4e3 Уведомления",
    "security": "\U0001f512 Безопасность",
}


def _fmt(prefs: dict, key: str) -> str:
    d = us.BY_KEY.get(key)
    if not d:
        return ""
    val = prefs.get(key, d.default)
    if d.type == "bool":
        return ("вкл" if val else "выкл") if d.title["ru"].lower() else str(val)
    if d.type == "choice":
        for value, titles in d.choices or []:
            if str(val) == value:
                return titles["ru"]
        return str(val) or "—"
    return str(val)


@router.message(F.text.startswith("/settings"))
async def cmd_settings(message: Message, state: FSMContext):
    uid, locale = await _ensure_user(message)
    await state.clear()
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    await message.answer(
        "\U0001f6e0 <b>Настройки</b>\n\nВыбери раздел:",
        reply_markup=settings_kb(prefs, locale),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:settings")
async def cb_settings(call: CallbackQuery):
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    await call.message.edit_text(
        "\U0001f6e0 <b>Настройки</b>\n\nВыбери раздел:",
        reply_markup=settings_kb(prefs, locale),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("set:sec:"))
async def cb_section(call: CallbackQuery):
    section = call.data.split(":")[-1]
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    defs = [d for d in us.SETTINGS if d.section == section]
    lines = [f"<b>{SECTION_TITLES.get(section, section)}</b>\n"]
    for d in defs:
        lines.append(f"• {d.title['ru']}: <b>{_fmt(prefs, d.key)}</b>")
    lines.append("\nНажми на параметр, чтобы изменить")
    await call.message.edit_text(
        "\n".join(lines), reply_markup=settings_kb(prefs, locale, section), parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data.startswith("set:val:"))
async def cb_value(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[-1]
    d = us.BY_KEY.get(key)
    if not d:
        await call.answer("unknown")
        return
    uid, locale = await _ensure_user(call.message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)

    if d.type == "bool":
        new = not bool(prefs.get(key, d.default))
        async with session_scope() as s:
            await us.set_value(s, uid, key, 1 if new else 0)
        await call.answer("Сохранено ✅")
        await _redraw(call, uid, d.section, state)
        return

    if d.type == "choice":
        labels = [titles["ru"] for _, titles in d.choices or []]
        current = str(prefs.get(key, d.default))
        values = [v for v, _ in d.choices or []]
        nxt = values[(values.index(current) + 1) % len(values)] if current in values else values[0]
        async with session_scope() as s:
            await us.set_value(s, uid, key, nxt)
        label = dict(d.choices or []).get(nxt, nxt)
        if isinstance(label, dict):
            label = label["ru"]
        await call.answer(f"Сохранено: {label} ✅")
        await _redraw(call, uid, d.section, state)
        return

    await state.set_state(WaitingValue.value)
    await state.update_data(key=key, section=d.section)
    hint = d.hint["ru"] if d.hint else ""
    rng = ""
    if d.min is not None or d.max is not None:
        rng = f"\nДиапазон: {d.min} — {d.max}"
    await call.message.answer(
        f"Введи новое значение для <b>{d.title['ru']}</b>\n"
        f"Сейчас: <b>{_fmt(prefs, key)}</b>{rng}\n{hint}",
        parse_mode="HTML",
        reply_markup=back_to_menu(locale),
    )
    await call.answer()


@router.message(WaitingValue.value)
async def on_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get("key", "")
    d = us.BY_KEY.get(key)
    await state.clear()
    if not d:
        await message.answer("Что-то пошло не так")
        return
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        ok, info = await us.set_value(s, uid, key, message.text.strip())
    if not ok:
        await message.answer(f"❌ Некорректное значение ({info}). Попробуй снова через /settings")
        return
    await message.answer(f"✅ {tr('saved', locale)} {d.title['ru']}: <b>{_fmt({key: info}, key)}</b>", parse_mode="HTML")


async def _redraw(call: CallbackQuery, uid: str, section: str, state: FSMContext) -> None:
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    defs = [d for d in us.SETTINGS if d.section == section]
    lines = [f"<b>{SECTION_TITLES.get(section, section)}</b>\n"]
    for d in defs:
        lines.append(f"• {d.title['ru']}: <b>{_fmt(prefs, d.key)}</b>")
    await call.message.edit_text(
        "\n".join(lines), reply_markup=settings_kb(prefs, "ru", section), parse_mode="HTML"
    )


@router.message(F.text.startswith("/set "))
async def cmd_set(message: Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: <code>/set ключ значение</code>", parse_mode="HTML")
        return
    _, key, value = parts
    uid, locale = await _ensure_user(message)
    async with session_scope() as s:
        ok, info = await us.set_value(s, uid, key.strip(), value.strip())
    if not ok:
        await message.answer(f"❌ {info}")
        return
    d = us.BY_KEY.get(key.strip())
    await message.answer(f"✅ {d.title['ru'] if d else key} = <b>{info}</b>", parse_mode="HTML")


@router.message(Command("panel", "web"))
async def cmd_panel(message: Message):
    uid, locale = await _ensure_user(message)
    await message.answer(
        "\U0001f310 <b>Веб-панель</b>\n\n"
        "Управление файлами, аккаунтами и всеми настройками:\n"
        f"{app_settings.public_base_url.rstrip('/')}",
        parse_mode="HTML",
    )
