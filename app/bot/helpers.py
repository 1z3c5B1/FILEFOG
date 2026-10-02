"""Утилиты бота: обёртки над БД, отправка документов."""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from app.core import user_settings as us
from app.db.models import User
from app.db.session import session_scope
from app.storage.factory import local_storage
from app.storage.local import human_size

log = logging.getLogger("trambot.bot")

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def get_bot() -> Bot | None:
    return _bot


async def locale_of(user: User) -> str:
    if user.locale in ("ru", "en"):
        return user.locale
    return "ru"


async def prefs_of(user_id: str) -> dict:
    async with session_scope() as s:
        return await us.load_all(s, user_id)


async def send_local_file(bot: Bot, user_id: str, chat_id: int, rel: str, caption: str = "") -> None:
    local = local_storage(user_id)
    # Читаем в память, а не отдаём путь: у R2 файла на диске нет вовсе.
    data = await local.read(rel)
    name = rel.rsplit("/", 1)[-1]
    await bot.send_document(
        chat_id,
        FSInputFile(io.BytesIO(data), filename=name),
        caption=caption or name,
    )


async def send_zip(bot: Bot, user_id: str, chat_id: int, folder: str = "") -> str:
    local = local_storage(user_id)
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in await local.walk():
            if folder and not rel.startswith(folder.rstrip("/") + "/"):
                continue
            z.writestr(rel, await local.read(rel))
            count += 1
    buf.seek(0)
    name = (folder.rsplit("/", 1)[-1] or "storage") + ".zip"
    await bot.send_document(
        chat_id,
        FSInputFile(io.BytesIO(buf.getvalue()), filename=name),
        caption=f"\U0001f5e2 {name} — {count} \U0001f4ce ({human_size(buf.getbuffer().nbytes)})",
    )
    return name


async def usage_line(user_id: str, prefs: dict) -> str:
    local = local_storage(user_id)
    used = await local.total_size()
    quota = int(prefs.get("user_quota_mb") or 0) * 1024 * 1024
    if quota:
        return f"\U0001f4ca {human_size(used)} / {human_size(quota)}"
    return f"\U0001f4ca {human_size(used)}"


async def send_linked_notification(tg_id: int, provider: str, login: str) -> None:
    bot = get_bot()
    if not bot:
        return
    titles = {"google": "Google", "github": "GitHub", "discord": "Discord", "yandex": "Яндекс"}
    await bot.send_message(
        tg_id,
        f"✅ Аккаунт <b>{titles.get(provider, provider)}</b> привязан"
        + (f"\n\U0001f464 {login}" if login else "")
        + "\n\nТеперь можно синхронизировать файлы → /sync",
        parse_mode="HTML",
    )


def target_path(prefs: dict, filename: str) -> str:
    folder = (prefs.get("uploads_folder") or "Uploads").strip().strip("/")
    if prefs.get("organize_by_type"):
        ext = Path(filename).suffix.lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            folder = f"{folder}/photos"
        elif ext in (".mp4", ".mkv", ".mov", ".avi", ".webm"):
            folder = f"{folder}/videos"
        elif ext in (".mp3", ".wav", ".flac", ".ogg", ".m4a"):
            folder = f"{folder}/audio"
        else:
            folder = f"{folder}/docs"
    return f"{folder}/{filename}".strip("/")
