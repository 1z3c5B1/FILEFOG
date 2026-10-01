"""Сборка Dispatcher и регистрация хендлеров."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from app.bot import handlers_core, handlers_files, handlers_settings, handlers_sync
from app.bot.helpers import set_bot
from app.bot.keyboards import APP_MENU_TEXT
from app.config import settings

log = logging.getLogger("trambot.bot")

COMMANDS = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="files", description="Список файлов"),
    BotCommand(command="upload", description="Загрузить файл"),
    BotCommand(command="get", description="Скачать файл"),
    BotCommand(command="all", description="Скачать всё архивом"),
    BotCommand(command="mkdir", description="Создать папку"),
    BotCommand(command="del", description="Удалить файл"),
    BotCommand(command="accounts", description="Мои аккаунты"),
    BotCommand(command="link", description="Войти через провайдера"),
    BotCommand(command="unlink", description="Отвязать провайдера"),
    BotCommand(command="sync", description="Синхронизация с облаком"),
    BotCommand(command="syncstatus", description="История синхронизации"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="set", description="Установить параметр"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="panel", description="Веб-панель"),
    BotCommand(command="help", description="Помощь"),
]


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(handlers_sync.router)
    dp.include_router(handlers_settings.router)
    dp.include_router(handlers_files.router)
    dp.include_router(handlers_core.router)
    return dp


def build_bot() -> Bot:
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


async def setup(bot: Bot, dp: Dispatcher) -> None:
    set_bot(bot)
    await bot.set_my_commands(COMMANDS)
    log.info("bot commands registered")
    await _setup_menu_button(bot)


async def _setup_menu_button(bot: Bot) -> None:
    """Кнопка меню слева от поля ввода открывает веб-панель (Mini App)."""
    if not settings.webapp_enabled:
        return
    url = settings.webapp_url
    if not url:
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text=APP_MENU_TEXT, web_app=WebAppInfo(url=url))
        )
        log.info("menu button -> %s", url)
    except TelegramBadRequest as e:  # напр. http вместо https
        log.warning("menu button not set: %s", e)
