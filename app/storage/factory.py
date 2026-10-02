"""Фабрика бэкендов хранилища."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.users import get_valid_access_token
from app.storage.base import RemoteFile
from app.storage.gdrive import GoogleDriveStorage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage
from app.storage.ydisk import YandexDiskStorage

log = logging.getLogger("trambot.storage")

__all__ = [
    "LocalStorage",
    "GoogleDriveStorage",
    "YandexDiskStorage",
    "S3Storage",
    "RemoteFile",
    "local_storage",
    "create_cloud_storage",
    "StorageUnavailable",
    "backend_name",
]


class StorageUnavailable(RuntimeError):
    """Нет токена, не настроен провайдер и т.п."""


def backend_name() -> str:
    return "s3" if settings.s3_ready else "local"


def local_storage(user_id: str) -> LocalStorage | S3Storage:
    """Хранилище пользователя: R2/S3, если настроено, иначе локальная папка.

    Имя функции оставлено прежним, чтобы не переписывать все вызовы: набор
    методов у обоих бэкендов одинаковый.
    """
    if settings.s3_ready:
        return S3Storage(
            user_id,
            bucket=settings.s3_bucket,
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region or "auto",
            prefix=settings.s3_prefix,
            service=settings.s3_service or "s3",
            timeout=settings.s3_timeout,
        )
    if settings.s3_configured_partially:
        log.warning(
            "S3 настроен частично - используется локальная папка. "
            "Проверь S3_BUCKET, S3_ENDPOINT, S3_ACCESS_KEY и S3_SECRET_KEY."
        )
    return LocalStorage(user_id)


async def create_cloud_storage(
    session: AsyncSession, user_id: str, provider: str, root: str = ""
) -> GoogleDriveStorage | YandexDiskStorage:
    if provider not in ("google", "yandex"):
        raise StorageUnavailable(f"unknown provider: {provider}")
    pair = await get_valid_access_token(session, user_id, provider)
    if not pair:
        raise StorageUnavailable(f"{provider}: нет доступа")
    token = pair[0]
    if provider == "google":
        return GoogleDriveStorage(token, root)
    return YandexDiskStorage(token, root)
