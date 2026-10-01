"""Фабрика бэкендов хранилища."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.users import get_valid_access_token
from app.storage.base import RemoteFile
from app.storage.gdrive import GoogleDriveStorage
from app.storage.local import LocalStorage
from app.storage.ydisk import YandexDiskStorage

__all__ = [
    "LocalStorage",
    "GoogleDriveStorage",
    "YandexDiskStorage",
    "RemoteFile",
    "local_storage",
    "create_cloud_storage",
    "StorageUnavailable",
]


class StorageUnavailable(RuntimeError):
    """Нет токена, не настроен провайдер и т.п."""


def local_storage(user_id: str) -> LocalStorage:
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
