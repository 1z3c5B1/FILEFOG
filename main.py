"""Точка входа: один процесс поднимает и aiogram-бота, и FastAPI-панель."""
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import uvicorn

from app.bot import build_bot, build_dispatcher, setup
from app.config import settings
from app.db.session import init_db
from app.sync import scheduler
from app.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("trambot")


def _log_polling_end(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    err = task.exception()
    if err is not None:
        log.error("telegram polling stopped: %s", err)
    else:
        log.info("telegram polling stopped")


@asynccontextmanager
async def lifespan(app):
    await init_db()
    log.info("database ready")

    bot = build_bot()
    dp = build_dispatcher()
    await setup(bot, dp)

    # polling идёт фоном, иначе lifespan не доходит до yield и веб не поднимается
    polling = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        name="aiogram-polling",
    )
    log.info("telegram bot started (@%s)", settings.bot_username)

    scheduler.start()
    polling.add_done_callback(_log_polling_end)

    try:
        yield
    finally:
        scheduler.shutdown()
        # Task.cancel() возвращает bool, его нельзя await-ить
        polling.cancel()
        try:
            await polling
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await bot.session.close()
        log.info("stopped")


def main() -> int:
    if not settings.bot_token or settings.bot_token.count(":") != 1:
        print("BOT_TOKEN не задан или неверный. Скопируй .env.example в .env", file=sys.stderr)
        return 1

    app = create_app(lifespan=lifespan)
    log.info("web panel: %s", settings.public_base_url)
    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
