"""Клавиатуры бота."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.core.i18n import tr
from app.core.oauth import ICONS, PROVIDERS, TITLES

APP_MENU_TEXT = "🚀 Открыть приложение"


def miniapp_button() -> InlineKeyboardButton | None:
    """Кнопка, открывающая веб-панель как Telegram Mini App."""
    if not settings.webapp_enabled:
        return None
    url = settings.webapp_url
    if not url:
        return None
    return InlineKeyboardButton(text=APP_MENU_TEXT, web_app=WebAppInfo(url=url))


def main_menu(locale: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    app = miniapp_button()
    if app:
        kb.row(app)
    kb.row(
        InlineKeyboardButton(text=tr("files", locale), callback_data="menu:files"),
        InlineKeyboardButton(text=tr("folder", locale), callback_data="menu:folders"),
        InlineKeyboardButton(text=tr("upload", locale), callback_data="menu:upload"),
    )
    kb.row(
        InlineKeyboardButton(text=tr("account", locale), callback_data="menu:accounts"),
        InlineKeyboardButton(text=tr("sync", locale), callback_data="menu:sync"),
    )
    kb.row(
        InlineKeyboardButton(text=tr("settings", locale), callback_data="menu:settings"),
        InlineKeyboardButton(text=tr("stats", locale), callback_data="menu:stats"),
    )
    return kb.as_markup()


def back_to_menu(locale: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=tr("back", locale), callback_data="menu"))
    return kb.as_markup()


def accounts_kb(linked: set[str], locale: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in PROVIDERS:
        if p in linked:
            kb.row(
                InlineKeyboardButton(
                    text=f"{ICONS[p]} {TITLES[p]} ✅", callback_data=f"acc:info:{p}"
                ),
                InlineKeyboardButton(text="🔓", callback_data=f"acc:unlink:{p}"),
            )
        else:
            kb.row(
                InlineKeyboardButton(
                    text=f"{ICONS[p]} {TITLES[p]}", callback_data=f"acc:login:{p}"
                )
            )
    kb.row(InlineKeyboardButton(text=tr("back", locale), callback_data="menu"))
    return kb.as_markup()


def files_kb(items: list, locale: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for it in items[:20]:
        name = it.path.rsplit("/", 1)[-1]
        icon = "\U0001f4c1" if it.mime == "inode/directory" else "\U0001f4ce"
        kb.row(InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"fs:open:{it.path}"))
    kb.row(
        InlineKeyboardButton(text=tr("upload", locale), callback_data="menu:upload"),
        InlineKeyboardButton(text="\U0001f5c2 ZIP", callback_data="fs:zipall"),
    )
    kb.row(InlineKeyboardButton(text=tr("back", locale), callback_data="menu"))
    return kb.as_markup()


def settings_kb(prefs: dict, locale: str = "ru", section: str = "") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not section:
        kb.row(
            InlineKeyboardButton(text="\U0001f6e0 Общие / General", callback_data="set:sec:general"),
            InlineKeyboardButton(text="\U0001f5c2 Файлы / Files", callback_data="set:sec:files"),
        )
        kb.row(
            InlineKeyboardButton(text="\U0001f504 Синхронизация / Sync", callback_data="set:sec:sync"),
            InlineKeyboardButton(text="\U0001f4e3 Уведомления / Notify", callback_data="set:sec:notify"),
        )
        kb.row(
            InlineKeyboardButton(text="\U0001f512 Безопасность / Sec", callback_data="set:sec:security")
        )
        kb.row(InlineKeyboardButton(text=tr("back", locale), callback_data="menu"))
        return kb.as_markup()

    from app.core.user_settings import SETTINGS

    for s in [x for x in SETTINGS if x.section == section]:
        val = prefs.get(s.key, s.default)
        title = s.title.get(locale) or s.title["ru"]
        if s.type == "bool":
            text = ("✅ " if val else "❌ ") + title
        else:
            text = f"{title}: {s.label(val, locale)}"
        kb.row(InlineKeyboardButton(text=text[:60], callback_data=f"set:val:{s.key}"))
    kb.row(
        InlineKeyboardButton(text=tr("back", locale), callback_data="menu:settings"),
        InlineKeyboardButton(text=tr("close", locale), callback_data="menu"),
    )
    return kb.as_markup()


def sync_kb(accounts: dict, prefs: dict, locale: str = "ru") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p, title in (("google", "Google Drive"), ("yandex", "Яндекс Диск")):
        mark = "✅" if accounts.get(p) else ""
        kb.row(InlineKeyboardButton(text=f"{title} {mark}", callback_data=f"sync:go:{p}"))
    kb.row(InlineKeyboardButton(text=tr("back", locale), callback_data="menu"))
    return kb.as_markup()
