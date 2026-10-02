"""Хендлеры файлов: загрузка, скачивание, папки, удаление."""
from __future__ import annotations

import io
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete

from app.bot.helpers import (
    send_local_file,
    send_zip,
    target_path,
    usage_line,
)
from app.bot.keyboards import back_to_menu, files_kb
from app.core import user_settings as us
from app.core.i18n import tr
from app.db.models import StoredFile
from app.db.session import session_scope
from app.storage.base import parent_of, safe_path
from app.storage.factory import local_storage
from app.storage.local import human_size

log = logging.getLogger("trambot.bot")
router = Router(name="files")


async def _uid_locale(message: Message) -> tuple[str, str]:
    from app.bot.handlers_core import _ensure_user

    return await _ensure_user(message)


@router.message(Command("files", "ls", "list"))
async def cmd_files(message: Message):
    uid, locale = await _uid_locale(message)
    local = local_storage(uid)
    items = await local.list("")
    if not items:
        await message.answer(tr("no_files", locale), reply_markup=back_to_menu(locale))
        return
    lines = ["\U0001f5c2 <b>Файлы</b>\n"]
    for it in items[:25]:
        if it.mime == "inode/directory":
            lines.append(f"\U0001f4c1 {it.path.rsplit('/',1)[-1]}/")
        else:
            lines.append(f"\U0001f4ce {it.path.rsplit('/',1)[-1]} — {human_size(it.size)}")
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    lines.append(f"\n{await usage_line(uid, prefs)}")
    await message.answer("\n".join(lines), reply_markup=files_kb(items, locale), parse_mode="HTML")


@router.callback_query(F.data.startswith("fs:open:"))
async def cb_open(call: CallbackQuery):
    uid, locale = await _uid_locale(call.message)
    path = call.data.split(":", 2)[-1]
    local = local_storage(uid)
    items = await local.list(path)
    lines = [f"\U0001f5c2 <b>{path or 'storage'}</b>\n"]
    if not items:
        lines.append("Пусто")
    for it in items[:25]:
        if it.mime == "inode/directory":
            lines.append(f"\U0001f4c1 {it.path.rsplit('/',1)[-1]}/")
        else:
            lines.append(f"\U0001f4ce {it.path.rsplit('/',1)[-1]} — {human_size(it.size)}")
    if parent_of(path):
        kb = files_kb(items, locale)
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        inline = list(kb.inline_keyboard)
        inline.insert(0, [InlineKeyboardButton(text="⬆️ ..", callback_data=f"fs:open:{parent_of(path)}")])
        kb = InlineKeyboardMarkup(inline_keyboard=inline)
    else:
        kb = files_kb(items, locale)
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await call.answer()


@router.callback_query(F.data == "menu:folders")
async def cb_folders(call: CallbackQuery):
    uid, locale = await _uid_locale(call.message)
    local = local_storage(uid)
    items = [it for it in await local.list("") if it.mime == "inode/directory"]
    if not items:
        await call.answer(tr("no_files", locale), show_alert=True)
        return
    lines = ["\U0001f4c1 <b>Папки</b>\n"] + [
        f"\U0001f4c1 {it.path}/" for it in items[:25]
    ]
    await call.message.edit_text(
        "\n".join(lines), reply_markup=files_kb(items, locale), parse_mode="HTML"
    )
    await call.answer()


@router.callback_query(F.data == "menu:stats")
async def cb_stats(call: CallbackQuery):
    from app.bot.handlers_core import cmd_stats

    await call.answer()
    await cmd_stats(call.message)


@router.callback_query(F.data.startswith("fs:zip:"))
async def cb_zip_one(call: CallbackQuery):
    uid, locale = await _uid_locale(call.message)
    rel = safe_path(call.data.split(":", 2)[-1])
    local = local_storage(uid)
    if await local.exists(rel) is None:
        await call.answer("Не найдено ❌", show_alert=True)
        return
    await call.answer("Архивирую…")
    await send_zip(call.bot, uid, call.message.chat.id, rel)


@router.callback_query(F.data == "fs:zipall")
async def cb_zipall(call: CallbackQuery):
    uid, locale = await _uid_locale(call.message)
    await call.answer("Архивирую…")
    try:
        await send_zip(call.bot, uid, call.message.chat.id, "")
    except Exception as e:  # noqa: BLE001
        await call.message.answer(f"Ошибка: {e}")


@router.callback_query(F.data == "menu:upload")
async def cb_upload(call: CallbackQuery):
    await call.message.answer(
        "\U0001f4e4 Пришли файл(ы) сюда — я сохраню в хранилище.\n"
        "Или укажи папку: <code>/upload Папка/Подпапка</code>"
    )
    await call.answer()


@router.message(Command("upload", "up"))
async def cmd_upload(message: Message, command: CommandObject):
    uid, locale = await _uid_locale(message)
    folder = (command.args or "").strip("/")
    await message.answer(
        f"Пришли файл — сохраню в <code>{folder or 'корень'}</code>"
    )
    message.bot_user_state = folder  # type: ignore[attr-defined]


@router.message(F.document | F.photo | F.video | F.audio | F.voice)
async def on_file(message: Message):
    uid, locale = await _uid_locale(message)
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)

    if isinstance(message.photo, list):
        file_id = message.photo[-1].file_id
        filename = f"photo_{message.photo[-1].file_unique_id[:8]}.jpg"
    else:
        obj = message.document or message.video or message.audio or message.voice
        if not obj:
            return
        file_id = obj.file_id
        filename = getattr(obj, "file_name", None) or f"{obj.file_unique_id[:8]}"

    folder = getattr(message, "bot_user_state", "") or ""
    rel = f"{folder}/{target_path(prefs, filename)}".strip("/") if folder else target_path(prefs, filename)
    rel = safe_path(rel)

    max_bytes = int(prefs["max_file_mb"]) * 1024 * 1024
    quota = int(prefs["user_quota_mb"]) * 1024 * 1024
    local = local_storage(uid)

    status = await message.answer("⏳ Скачиваю…")
    buf = io.BytesIO()
    await message.bot.download(file_id, destination=buf)
    data = buf.getvalue()

    if len(data) > max_bytes:
        await status.edit_text(tr("file_too_big", locale, limit=prefs["max_file_mb"]))
        return
    if quota and await local.total_size() + len(data) > quota:
        await status.edit_text(tr("quota", locale))
        return

    await local.upload(rel, data)
    if prefs.get("notify_uploads"):
        await status.edit_text(f"✅ Сохранено: <code>{rel}</code>\n{human_size(len(data))}")
    else:
        await status.delete()
        await message.answer(f"✅ <code>{rel}</code>", parse_mode="HTML")


@router.message(Command("get", "download", "dl"))
async def cmd_get(message: Message, command: CommandObject):
    uid, locale = await _uid_locale(message)
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Укажи путь: <code>/get Uploads/file.pdf</code>", parse_mode="HTML")
        return
    local = local_storage(uid)
    rel = safe_path(arg)
    found = await local.exists(rel)
    if found is None or found.mime == "inode/directory":
        await message.answer("Файл не найден ❌")
        return
    await message.answer("⏳ Отправляю…")
    await send_local_file(message.bot, uid, message.chat.id, rel)


@router.message(Command("all", "zip"))
async def cmd_all(message: Message, command: CommandObject):
    uid, locale = await _uid_locale(message)
    folder = (command.args or "").strip("/")
    await message.answer("⏳ Собираю архив…")
    await send_zip(message.bot, uid, message.chat.id, folder)


@router.message(Command("mkdir", "folder"))
async def cmd_mkdir(message: Message, command: CommandObject):
    uid, locale = await _uid_locale(message)
    name = (command.args or "").strip("/")
    if not name:
        await message.answer("Укажи имя папки: <code>/mkdir Photos</code>", parse_mode="HTML")
        return
    await local_storage(uid).mkdir(name)
    await message.answer(f"\U0001f4c1 Создано: <code>{name}</code>", parse_mode="HTML")


@router.message(Command("del", "delete", "rm"))
async def cmd_del(message: Message, command: CommandObject):
    uid, locale = await _uid_locale(message)
    arg = (command.args or "").strip()
    if not arg:
        await message.answer("Укажи путь: <code>/del Uploads/file.pdf</code>", parse_mode="HTML")
        return
    async with session_scope() as s:
        prefs = await us.load_all(s, uid)
    if prefs.get("ask_before_delete") and not (command.args or "").startswith("!"):
        await message.answer(
            f"Удалить <code>{arg}</code>?\nПовтори с <code>/del! {arg}</code> для подтверждения",
            parse_mode="HTML",
        )
        return
    rel = safe_path(arg.lstrip("!").strip())
    local = local_storage(uid)
    if await local.exists(rel) is None:
        await message.answer("Не найдено ❌")
        return
    await local.delete(rel)
    async with session_scope() as s:
        await s.execute(
            delete(StoredFile).where(StoredFile.user_id == uid, StoredFile.rel_path == rel)
        )
    await message.answer(f"\U0001f5d1 Удалено: <code>{rel}</code>", parse_mode="HTML")
