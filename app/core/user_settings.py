"""Реестр настроек: типы, значения по умолчанию, валидация."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserSettings


@dataclass
class SettingDef:
    key: str
    type: str  # bool | int | str | choice
    default: Any
    title: dict[str, str]
    hint: dict[str, str] = field(default_factory=dict)
    choices: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    min: int | None = None
    max: int | None = None
    section: str = "general"

    def cast(self, raw: Any) -> Any:
        if raw is None or raw == "":
            return self.default
        try:
            if self.type == "bool":
                return str(raw) in ("1", "true", "True", "on", "yes")
            if self.type == "int":
                return int(float(raw))
            return str(raw)
        except (TypeError, ValueError):
            return self.default

    def check(self, value: Any) -> tuple[bool, str]:
        if self.type == "int":
            if self.min is not None and value < self.min:
                return False, f"min={self.min}"
            if self.max is not None and value > self.max:
                return False, f"max={self.max}"
        elif self.type == "choice" and self.choices:
            valid = {c[0] for c in self.choices}
            if str(value) not in valid:
                return False, "допустимо: " + ", ".join(sorted(valid))
        elif self.type == "str":
            if not isinstance(value, str):
                return False, "ожидается строка"
            if len(value) > 2000:
                return False, "максимум 2000 символов"
        return True, ""

    def label(self, value: Any, locale: str = "ru") -> str:
        if self.type == "bool":
            return "вкл" if value else "выкл"
        if self.type == "choice":
            for val, titles in self.choices:
                if str(val) == str(value):
                    return titles.get(locale) or titles.get("ru") or val
            return str(value) or "—"
        return str(value) if value != "" else "—"

    def label_ru(self, value: Any) -> str:
        return self.label(value, "ru")


def S(
    key: str,
    type_: str,
    default: Any,
    ru: str,
    en: str,
    hint_ru: str = "",
    hint_en: str = "",
    *,
    section: str = "general",
    choices: list[tuple[str, str, str]] | None = None,
    min: int | None = None,
    max: int | None = None,
) -> SettingDef:
    return SettingDef(
        key=key,
        type=type_,
        default=default,
        title={"ru": ru, "en": en},
        hint={"ru": hint_ru, "en": hint_en},
        choices=[(c[0], {"ru": c[1], "en": c[2]}) for c in choices] if choices else [],
        min=min,
        max=max,
        section=section,
    )


SETTINGS: list[SettingDef] = [
    # ---------------- Общие ----------------
    S("locale", "choice", "ru", "Язык интерфейса", "Interface language", section="general",
      choices=[("ru", "Русский", "Russian"), ("en", "Английский", "English")]),
    S("theme", "choice", "dark", "Тема оформления", "Theme", section="general",
      choices=[("dark", "Тёмная", "Dark"), ("light", "Светлая", "Light"), ("auto", "Как в системе", "System")]),
    S("show_browser_menu", "bool", True, "Кнопка меню в браузере", "Browser menu button",
      "Показывать /menu в списке команд", "Show /menu in the command list", section="general"),
    S("notify_uploads", "bool", True, "Уведомлять о загрузках", "Notify on uploads", section="general"),
    S("emoji_interface", "bool", True, "Эмодзи в интерфейсе", "Emoji in interface", section="general"),

    # ---------------- Файлы ----------------
    S("uploads_folder", "str", "Uploads", "Папка для загрузок", "Uploads folder",
      "Куда складываются файлы из Telegram", "Where Telegram files are stored", section="files"),
    S("max_file_mb", "int", 100, "Лимит на файл, МБ", "Max file size, MB",
      "Большие файлы отклоняются", "Bigger files are rejected", section="files", min=1, max=2048),
    S("user_quota_mb", "int", 2048, "Квота, МБ", "Quota, MB",
      "0 — без лимита", "0 = unlimited", section="files", min=0, max=1_000_000),
    S("keep_original_name", "bool", True, "Хранить исходные имена", "Keep original names", section="files"),
    S("organize_by_type", "bool", False, "Сортировать по типу файла", "Group files by type",
      "photos/, videos/, audio/, docs/", "photos/, videos/, audio/, docs/", section="files"),
    S("ask_before_delete", "bool", True, "Спрашивать перед удалением", "Confirm before delete", section="files"),
    S("trash_enabled", "bool", True, "Корзина вместо удаления", "Trash instead of delete",
      "Удалённые файлы уходят в .trash", "Deleted files move to .trash", section="files"),
    S("zip_multiple_download", "bool", True, "Скачивать архивом при /all", "Send archive for /all", section="files"),
    S("thumbnails", "bool", True, "Превью фото и видео", "Photo/video previews", section="files"),

    # ---------------- Синхронизация ----------------
    S("default_provider", "choice", "", "Облако по умолчанию", "Default cloud",
      "Используется, если не выбран другой", "Used when no other provider is picked", section="sync",
      choices=[("", "Не выбрано", "Not selected"), ("google", "Google Drive", "Google Drive"),
               ("yandex", "Яндекс Диск", "Yandex Disk")]),
    S("sync_folder", "str", "Trambot", "Корневая папка синхронизации", "Sync root folder", section="sync"),
    S("auto_sync", "bool", False, "Автосинхронизация", "Auto sync", section="sync"),
    S("auto_sync_interval_min", "int", 60, "Интервал автосинхронизации, мин", "Auto sync interval, min",
      "Работает при включённой автосинхронизации", "Requires auto sync enabled",
      section="sync", min=5, max=1440),
    S("sync_on_start", "bool", True, "Синхронизировать при /start", "Sync on /start", section="sync"),
    S("sync_both_ways", "bool", True, "Двусторонняя синхронизация", "Two-way sync",
      "Иначе только отправка в облако", "Otherwise upload-only", section="sync"),
    S("sync_delete_propagate", "bool", False, "Удалять в облаке при удалении локально",
      "Propagate deletions to cloud", "Осторожно: удаление необратимо", "Careful: this is destructive",
      section="sync"),
    S("sync_conflicts", "choice", "newer", "Конфликты версий", "Version conflicts",
      "Что делать, если файл изменён в обоих местах", "What to do when changed in both places",
      section="sync",
      choices=[("newer", "Оставить более новый", "Keep newer"),
               ("local", "Всегда локальный", "Always local"),
               ("remote", "Всегда из облака", "Always remote"),
               ("both", "Сохранить обе версии", "Keep both copies")]),
    S("sync_max_file_mb", "int", 0, "Лимит файла для синхронизации, МБ", "Sync file size limit, MB",
      "0 — без лимита", "0 = unlimited", section="sync", min=0, max=8192),
    S("checksum_verify", "bool", True, "Проверять целостность (MD5)", "Verify integrity (MD5)", section="sync"),

    # ---------------- Уведомления ----------------
    S("notify_sync_done", "bool", True, "Уведомлять о завершении синхронизации", "Notify when sync finishes",
      section="notify"),
    S("notify_errors", "bool", True, "Уведомлять об ошибках", "Notify about errors", section="notify"),
    S("notify_quota", "bool", True, "Предупреждать о заполнении квоты", "Warn about quota",
      "При заполнении больше чем на 90%", "When 90%+ full", section="notify"),
    S("daily_report", "bool", False, "Ежедневный отчёт", "Daily report",
      "Статистика хранилища в 09:00", "Storage stats at 09:00", section="notify"),

    # ---------------- Безопасность ----------------
    S("web_session_hours", "int", 168, "Срок сессии веб-панели, ч", "Web panel session, h",
      section="security", min=1, max=720),
    S("hide_email", "bool", True, "Скрывать email в панели", "Hide email in panel", section="security"),
    S("allowed_origins", "str", "", "Разрешённые домены (через запятую)", "Allowed domains (comma separated)",
      section="security"),
    S("2fa_required", "bool", False, "Требовать код при входе", "Require code on login", section="security"),
]

BY_KEY: dict[str, SettingDef] = {s.key: s for s in SETTINGS}

SECTIONS: list[tuple[str, dict[str, str]]] = [
    ("general", {"ru": "Общие", "en": "General"}),
    ("files", {"ru": "Файлы", "en": "Files"}),
    ("sync", {"ru": "Синхронизация", "en": "Sync"}),
    ("notify", {"ru": "Уведомления", "en": "Notifications"}),
    ("security", {"ru": "Безопасность", "en": "Security"}),
]

DEFAULTS: dict[str, Any] = {s.key: s.default for s in SETTINGS}


async def load_all(session: AsyncSession, user_id: str) -> dict[str, Any]:
    rows = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    stored = {r.key: r.value for r in rows.scalars()}
    out = dict(DEFAULTS)
    for key, raw in stored.items():
        d = BY_KEY.get(key)
        if d:
            out[key] = d.cast(raw)
    return out


async def get(session: AsyncSession, user_id: str, key: str) -> Any:
    d = BY_KEY.get(key)
    if not d:
        return None
    res = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user_id, UserSettings.key == key)
    )
    found = res.scalar_one_or_none()
    return d.cast(found.value) if found else d.default


async def set_value(session: AsyncSession, user_id: str, key: str, value: Any) -> tuple[bool, str]:
    d = BY_KEY.get(key)
    if not d:
        return False, "неизвестный параметр"

    if d.type == "bool":
        if isinstance(value, bool):
            raw = "1" if value else "0"
        else:
            raw = "1" if str(value) in ("1", "true", "on", "yes", "True", "вкл") else "0"
    elif d.type == "int":
        try:
            raw = str(int(float(str(value).replace(",", "."))))
        except (TypeError, ValueError):
            return False, "ожидается число"
    else:
        raw = str(value)[:2000]

    ok, info = d.check(d.cast(raw))
    if not ok:
        return False, info

    res = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user_id, UserSettings.key == key)
    )
    row = res.scalar_one_or_none()
    if row is None:
        session.add(UserSettings(user_id=user_id, key=key, value=raw))
    else:
        row.value = raw
    await session.flush()
    return True, raw


async def set_many(session: AsyncSession, user_id: str, values: dict[str, Any]) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    for k, v in values.items():
        out.append((k, *await set_value(session, user_id, k, v)))
    return out
