"""Движок БД и фабрика сессий."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base


def build_engine_url(raw: str, db_path: str) -> str:
    """Приводит DATABASE_URL к схеме, понятной SQLAlchemy.

    Провайдеры отдают ссылку как `postgres://` или `postgresql://` без драйвера,
    а SQLAlchemy так не понимает и падает. Async-драйвер у нас один - asyncpg.
    """
    raw = (raw or "").strip()
    if not raw:
        return f"sqlite+aiosqlite:///{db_path}"
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if raw.startswith("postgresql+asyncpg://") or raw.startswith("sqlite+aiosqlite://"):
        return raw
    if raw.startswith("postgresql://"):
        return "postgresql+asyncpg://" + raw[len("postgresql://") :]
    scheme, sep, rest = raw.partition("://")
    if sep and scheme.startswith("postgresql+"):
        # например postgresql+psycopg2://u:p@host/db -> asyncpg, хвост целиком
        return "postgresql+asyncpg://" + rest
    return raw


def is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


engine_url = build_engine_url(settings.database_url, settings.db_path)

if is_sqlite(engine_url):
    # Каталог для SQLite-файла; для внешней базы он не нужен.
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(
        engine_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
else:
    engine = create_async_engine(
        engine_url,
        echo=False,
        future=True,
        pool_pre_ping=True,  # рвётся соединение на free-уровне - проверяем
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
    )

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Контекст-менеджер для фоновых задач и хендлеров бота."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
