"""Простая i18n-словарь для бота и веб-панели."""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "menu": {"ru": "\U0001f527 Меню", "en": "\U0001f527 Menu"},
    "menu_title": {
        "ru": "\U0001f9f0 Trambot — ваше облачное хранилище в Telegram",
        "en": "\U0001f9f0 Trambot — your cloud storage inside Telegram",
    },
    "menu_text": {
        "ru": (
            "<b>Что умеем:</b>\n"
            "• Хранилище файлов и папок\n"
            "• Вход через Google, GitHub, Discord, Яндекс\n"
            "• Синхронизация с Google Drive и Яндекс Диском\n"
            "• Много настроек под себя\n\n"
            "Выбери раздел 👇"
        ),
        "en": (
            "<b>Features:</b>\n"
            "• File & folder storage\n"
            "• Sign in with Google, GitHub, Discord, Yandex\n"
            "• Sync with Google Drive and Yandex Disk\n"
            "• Tons of settings\n\n"
            "Pick a section 👇"
        ),
    },
    "account": {"ru": "\U0001f464 Аккаунты", "en": "\U0001f464 Accounts"},
    "files": {"ru": "\U0001f5c2 Файлы", "en": "\U0001f5c2 Files"},
    "settings": {"ru": "\U0001f6e0 Настройки", "en": "\U0001f6e0 Settings"},
    "sync": {"ru": "\U0001f504 Синхронизация", "en": "\U0001f504 Sync"},
    "upload": {"ru": "\U0001f4e4 Загрузить", "en": "\U0001f4e4 Upload"},
    "download": {"ru": "\U0001f4e5 Скачать", "en": "\U0001f4e5 Download"},
    "folder": {"ru": "\U0001f4c1 Папки", "en": "\U0001f4c1 Folders"},
    "stats": {"ru": "\U0001f4ca Статистика", "en": "\U0001f4ca Stats"},
    "help": {"ru": "❓ Помощь", "en": "❓ Help"},
    "back": {"ru": "⬅️ Назад", "en": "⬅️ Back"},
    "close": {"ru": "✖️ Закрыть", "en": "✖️ Close"},
    "no_files": {"ru": "Файлов пока нет", "en": "No files yet"},
    "not_linked": {
        "ru": "Аккаунт не привязан",
        "en": "Account is not linked",
    },
    "linked": {"ru": "Привязан", "en": "Linked"},
    "need_account": {
        "ru": "Сначала войди через провайдера 👆",
        "en": "Sign in with a provider first 👆",
    },
    "file_too_big": {
        "ru": "Файл больше лимита ({limit} МБ)",
        "en": "File exceeds limit ({limit} MB)",
    },
    "quota": {"ru": "Квота исчерпана", "en": "Quota exceeded"},
    "saved": {"ru": "Сохранено ✅", "en": "Saved ✅"},
    "sync_done": {
        "ru": "Синхронизация завершена",
        "en": "Sync finished",
    },
    "err_generic": {
        "ru": "Что-то пошло не так: {err}",
        "en": "Something went wrong: {err}",
    },
    "login_choose": {
        "ru": "Выбери способ входа:",
        "en": "Choose a sign-in method:",
    },
    "welcome": {
        "ru": "Привет, <b>{name}</b>!",
        "en": "Hi, <b>{name}</b>!",
    },
    "web_panel": {"ru": "\U0001f310 Веб-панель", "en": "\U0001f310 Web panel"},
    "usage": {
        "ru": "Занято {used} из {total}",
        "en": "Used {used} of {total}",
    },
    "free": {"ru": "Свободно", "en": "Free"},
}


class _SafeDict(dict):
    """Подстановка без KeyError: неизвестный ключ даёт пустую строку."""

    def __missing__(self, key):
        return ""


def tr(key: str, locale: str = "ru", **kwargs) -> str:
    data = STRINGS.get(key)
    if not data:
        return key
    text = data.get(locale) or data.get("ru") or key
    if kwargs:
        return text.format_map(_SafeDict(kwargs))
    return text


def pick(d: dict[str, str], locale: str = "ru") -> str:
    return d.get(locale) or d.get("ru") or next(iter(d.values()), "")
