"""Фоновая автосинхронизация (APScheduler)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core import user_settings as us
from app.db.models import User
from app.db.session import session_scope
from app.sync.engine import sync_user

log = logging.getLogger("trambot.scheduler")

scheduler = AsyncIOScheduler(timezone="UTC")
_registered: set[str] = set()


async def run_sync_for(user_id: str, provider: str | None = None) -> None:
    async with session_scope() as session:
        try:
            await sync_user(session, user_id, provider)
        except Exception:  # noqa: BLE001
            log.exception("auto sync failed for %s", user_id)


async def _tick() -> None:
    async with session_scope() as session:
        res = await session.execute(select(User.id).where(User.active.is_(True)))
        user_ids = list(res.scalars())
    for uid in user_ids:
        try:
            async with session_scope() as session:
                prefs = await us.load_all(session, uid)
                if not prefs.get("auto_sync") or not prefs.get("default_provider"):
                    continue
                r = await sync_user(session, uid, prefs["default_provider"])
                log.info("auto sync %s: %s", uid, r.summary())
        except Exception:  # noqa: BLE001
            log.exception("tick failed for %s", uid)


def start() -> None:
    if not scheduler.running:
        scheduler.add_job(
            _tick,
            IntervalTrigger(minutes=5),
            id="auto_sync_scanner",
            replace_existing=True,
            max_instances=1,
        )
        scheduler.start()
        log.info("scheduler started")


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
